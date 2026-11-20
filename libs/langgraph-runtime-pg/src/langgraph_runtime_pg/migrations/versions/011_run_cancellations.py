"""Durable fixed-target cancellation receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "011_run_cancellations"
down_revision = "010_run_queue_position"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_cancellations",
        sa.Column("cancellation_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("principal", sa.Text(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("targets", JSONB(), nullable=False),
        sa.Column("reconcile_run_ids", JSONB(), nullable=False),
        sa.Column("has_pending_interrupts", sa.Boolean(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("thread_id", "principal", "idempotency_key"),
    )
    op.create_index("ix_run_cancellations_thread_id", "run_cancellations", ["thread_id"])


def downgrade() -> None:
    op.drop_table("run_cancellations")
