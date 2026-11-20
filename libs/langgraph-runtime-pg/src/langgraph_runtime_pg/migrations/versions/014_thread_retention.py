"""Explicit thread retention and durable deletion handoff."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "014_thread_retention"
down_revision = "013_run_acceptance_receipts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("retention", JSONB(none_as_null=True)))
    for name in ("last_activity_at", "expires_at", "deletion_requested_at"):
        op.add_column("threads", sa.Column(name, sa.DateTime(timezone=True)))
    op.create_index("ix_threads_expires_at", "threads", ["expires_at"])
    op.create_table(
        "thread_deletions",
        sa.Column("thread_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("hooks", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(256)),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_thread_deletions_due", "thread_deletions", ["state", "next_attempt_at"])


def downgrade() -> None:
    raise RuntimeError("Retain deletion handoffs and tombstones when rolling back application code")
