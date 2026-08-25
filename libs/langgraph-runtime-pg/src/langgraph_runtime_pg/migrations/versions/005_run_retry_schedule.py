"""Add the durable retry schedule for infrastructure failures."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005_run_retry_schedule"
down_revision: str | Sequence[str] | None = "004_thread_event_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_runs_next_attempt_at", "runs", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_index("ix_runs_next_attempt_at", table_name="runs")
    op.drop_column("runs", "next_attempt_at")
