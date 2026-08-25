"""Add production run ownership, authorization scope, and durable event cursors."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002_production_runtime"
down_revision: str | Sequence[str] | None = "001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable scope columns preserve existing local data; production writes them from Principal.
    for table in ("assistants", "threads", "runs"):
        op.add_column(table, sa.Column("tenant_id", sa.String(length=128), nullable=True))
        op.add_column(table, sa.Column("project_id", sa.String(length=128), nullable=True))

    op.add_column("runs", sa.Column("reason", sa.String(length=64), nullable=True))
    op.add_column(
        "runs", sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False)
    )
    op.add_column(
        "runs", sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False)
    )
    op.add_column("runs", sa.Column("idempotency_key", sa.String(length=256), nullable=True))
    op.add_column("runs", sa.Column("lease_owner", sa.String(length=256), nullable=True))
    op.add_column("runs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "runs", sa.Column("event_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False)
    )
    op.create_index("ix_runs_scope_status", "runs", ["tenant_id", "project_id", "status"])
    op.create_index(
        "uq_runs_scope_idempotency",
        "runs",
        ["tenant_id", "project_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "run_leases",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner", sa.String(length=256), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_run_leases_expires_at", "run_leases", ["expires_at"])

    op.create_table(
        "runtime_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("thread_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column(
            "namespace",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["thread_id"], ["threads.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_runtime_events_run_sequence"),
    )
    op.create_index("ix_runtime_events_thread_seq", "runtime_events", ["thread_id", "sequence"])
    op.create_index("ix_runtime_events_created_at", "runtime_events", ["created_at"])

    op.create_table(
        "runtime_schema",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.execute(
        sa.text(
            "INSERT INTO runtime_schema (key, value) VALUES ('contract', 'production-v1') "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
        )
    )


def downgrade() -> None:
    op.drop_table("runtime_schema")
    op.drop_index("ix_runtime_events_created_at", table_name="runtime_events")
    op.drop_index("ix_runtime_events_thread_seq", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_index("ix_run_leases_expires_at", table_name="run_leases")
    op.drop_table("run_leases")
    op.drop_index("uq_runs_scope_idempotency", table_name="runs")
    op.drop_index("ix_runs_scope_status", table_name="runs")
    for name in (
        "event_seq",
        "heartbeat_at",
        "lease_expires_at",
        "lease_owner",
        "idempotency_key",
        "max_attempts",
        "retry_count",
        "reason",
    ):
        op.drop_column("runs", name)
    for table in ("runs", "threads", "assistants"):
        op.drop_column(table, "project_id")
        op.drop_column(table, "tenant_id")
