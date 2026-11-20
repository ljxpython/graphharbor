"""Replace history copies with transaction-local undo records (one-time conversion)."""

import jsonpatch
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "015_checkpoint_journal"
down_revision = "014_thread_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM runs WHERE lease_expires_at>now())")):
        raise RuntimeError(
            "drain API/workers and back up the database before checkpoint conversion"
        )
    op.add_column(
        "run_checkpoint_baselines",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), nullable=False),
    )
    op.add_column("run_checkpoint_baselines", sa.Column("heads", JSONB, nullable=True))
    op.alter_column("run_checkpoint_baselines", "thread_id", nullable=True)
    bind.execute(
        sa.text("""
        WITH ordered AS (SELECT run_id, row_number() OVER (
            ORDER BY projection->>'_captured_at', run_id) AS n FROM run_checkpoint_baselines)
        UPDATE run_checkpoint_baselines b SET sequence=o.n FROM ordered o WHERE b.run_id=o.run_id
    """)
    )
    op.create_index(
        "ix_run_checkpoint_boundaries_order", "run_checkpoint_baselines", ["thread_id", "sequence"]
    )
    op.create_table(
        "run_checkpoint_undo",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("run_checkpoint_baselines.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("table_name", sa.Text(), primary_key=True),
        sa.Column("row_key", JSONB, primary_key=True),
        sa.Column("before_image", JSONB(none_as_null=True)),
    )
    # Page IDs only. Historical payloads stay inside PostgreSQL during conversion.
    cursor = 0
    while True:
        rows = (
            bind.execute(
                sa.text("""
            SELECT run_id, thread_id, sequence, projection FROM run_checkpoint_baselines
            WHERE sequence>:cursor ORDER BY sequence LIMIT 1
        """),
                {"cursor": cursor},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        row = rows[0]
        cursor = row["sequence"]
        params = {"run_id": row["run_id"], "thread_id": str(row["thread_id"]), "sequence": cursor}
        after_projection = bind.scalar(
            sa.text("""
            SELECT projection FROM run_checkpoint_baselines
            WHERE thread_id=CAST(:thread_id AS uuid) AND sequence>:sequence
            ORDER BY sequence LIMIT 1
        """),
            params,
        )
        after_values = (
            after_projection["values_"]
            if after_projection
            else bind.scalar(
                sa.text('SELECT "values" FROM threads WHERE thread_id=CAST(:thread_id AS uuid)'),
                params,
            )
        )
        projection = row["projection"]
        projection["values_undo"] = jsonpatch.make_patch(
            after_values, projection.pop("values_")
        ).patch
        for table, column, keys in (
            ("checkpoints", "checkpoints", ("thread_id", "checkpoint_ns", "checkpoint_id")),
            (
                "checkpoint_writes",
                "writes",
                ("thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx"),
            ),
        ):
            key_json = ", ".join(f"'{k}', COALESCE(a.r,b.r)->'{k}'" for k in keys)
            matches = " AND ".join(f"a.r->'{k}'=b.r->'{k}'" for k in keys)
            changes = f"""
                WITH versions AS (
                    SELECT old.{column} AS before_rows, COALESCE(
                        (SELECT next.{column} FROM run_checkpoint_baselines next
                            WHERE next.thread_id=old.thread_id AND next.sequence>old.sequence
                            ORDER BY next.sequence LIMIT 1),
                        (SELECT jsonb_agg(to_jsonb(t)) FROM {table} t
                            WHERE t.thread_id=:thread_id), '[]'::jsonb) AS after_rows
                    FROM run_checkpoint_baselines old WHERE old.run_id=:run_id
                ), before_rows AS (SELECT jsonb_array_elements(before_rows) r FROM versions),
                after_rows AS (SELECT jsonb_array_elements(after_rows) r FROM versions),
                changes AS (SELECT jsonb_build_object({key_json}) AS row_key,
                    a.r AS before_image, b.r AS after_image
                    FROM before_rows a FULL JOIN after_rows b ON {matches}
                    WHERE a.r IS DISTINCT FROM b.r)
            """
            if table == "checkpoints" and bind.scalar(
                sa.text(
                    changes
                    + """
                SELECT EXISTS(SELECT 1 FROM changes WHERE after_image IS NOT NULL
                    AND COALESCE(after_image->'metadata'->>'run_id','')<>CAST(:run_id AS text))
            """
                ),
                params,
            ):
                raise RuntimeError(
                    f"cannot prove checkpoint ownership for run {row['run_id']}; restore backup and investigate"
                )
            bind.execute(
                sa.text(
                    changes
                    + f"""
                INSERT INTO run_checkpoint_undo(run_id,table_name,row_key,before_image)
                SELECT :run_id, '{table}', row_key, before_image FROM changes
            """
                ),
                params,
            )
        # Keep only namespaces touched by this run, plus the root boundary.
        bind.execute(
            sa.text("""
            UPDATE run_checkpoint_baselines b SET heads=COALESCE((
                SELECT jsonb_object_agg(ns, id) FROM (
                    SELECT DISTINCT ON (c->>'checkpoint_ns') c->>'checkpoint_ns' AS ns,
                        c->>'checkpoint_id' AS id FROM jsonb_array_elements(b.checkpoints) c
                    WHERE c->>'checkpoint_ns'='' OR EXISTS(
                        SELECT 1 FROM run_checkpoint_undo u WHERE u.run_id=b.run_id
                            AND u.row_key->>'checkpoint_ns'=c->>'checkpoint_ns')
                    ORDER BY c->>'checkpoint_ns', c->>'checkpoint_id' DESC
                ) heads), '{}'::jsonb), projection=CAST(:projection AS jsonb) WHERE b.run_id=:run_id
        """),
            {**params, "projection": __import__("json").dumps(projection)},
        )
    op.alter_column("run_checkpoint_baselines", "heads", nullable=False)
    op.drop_column("run_checkpoint_baselines", "checkpoints")
    op.drop_column("run_checkpoint_baselines", "writes")


def downgrade() -> None:
    raise RuntimeError(
        "checkpoint journal conversion is forward-only; restore the pre-conversion backup"
    )
