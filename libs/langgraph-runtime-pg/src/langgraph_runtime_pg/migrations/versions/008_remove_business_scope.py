"""Remove platform-owned tenant/project columns from the generic runtime."""

import sqlalchemy as sa
from alembic import op

revision = "008_remove_business_scope"
down_revision = "007_checkpoint_baselines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    for table in ("assistants", "threads", "runs", "crons"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(f"{table} must be empty before removing business scope")
    for table in ("assistants", "threads", "runs", "crons"):
        if table == "runs":
            op.drop_index("ix_runs_scope_status", table_name=table)
            op.drop_index("uq_runs_scope_idempotency", table_name=table)
        if table == "crons":
            op.drop_index("ix_crons_scope_enabled", table_name=table)
        for column in ("tenant_id", "project_id"):
            op.drop_column(table, column)
    op.create_index(
        "uq_runs_idempotency",
        "runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    raise RuntimeError("Business scope columns were intentionally removed; restore from backup")
