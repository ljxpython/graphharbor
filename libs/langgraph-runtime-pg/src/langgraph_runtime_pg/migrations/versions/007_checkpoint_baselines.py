"""Persist run rollback baselines without changing upstream checkpoint tables."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "007_checkpoint_baselines"
down_revision = "006_terminal_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_checkpoint_baselines",
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("runs.run_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("threads.thread_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoints", JSONB(), nullable=False),
        sa.Column("writes", JSONB(), nullable=False),
        sa.Column("projection", JSONB(), nullable=False),
    )
    op.create_index(
        "ix_run_checkpoint_baselines_thread_id", "run_checkpoint_baselines", ["thread_id"]
    )


def downgrade() -> None:
    op.drop_table("run_checkpoint_baselines")
