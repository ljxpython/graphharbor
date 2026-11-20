"""Independent durable webhook delivery snapshots."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "012_run_callback_deliveries"
down_revision = "011_run_cancellations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_callback_deliveries",
        sa.Column("delivery_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=True),
        sa.Column("profile", sa.String(16), nullable=False),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retry_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_http_status", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "profile", name="uq_run_callback_profile"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_callback_due", "run_callback_deliveries", ["state", "next_attempt_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    raise RuntimeError("Retain webhook delivery snapshots when rolling back application code")
