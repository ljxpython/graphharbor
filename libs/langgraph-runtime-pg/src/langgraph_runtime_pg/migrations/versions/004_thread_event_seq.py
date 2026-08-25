"""Add a durable per-thread event cursor for Protocol event streams."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004_thread_event_seq"
down_revision: str | Sequence[str] | None = "003_cron_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column("event_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("threads", "event_seq")
