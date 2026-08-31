"""Make terminal run events durable and idempotent."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006_terminal_events"
down_revision: str | Sequence[str] | None = "005_run_retry_schedule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_events",
        sa.Column("terminal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "uq_runtime_events_run_terminal",
        "runtime_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text("terminal = true AND run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_runtime_events_run_terminal", table_name="runtime_events")
    op.drop_column("runtime_events", "terminal")
