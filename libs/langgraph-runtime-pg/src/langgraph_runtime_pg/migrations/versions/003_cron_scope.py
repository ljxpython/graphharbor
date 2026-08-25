"""Add Principal scope to cron resources."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003_cron_scope"
down_revision: str | Sequence[str] | None = "002_production_runtime"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("threads", sa.Column("graph_id", sa.String(length=256), nullable=True))
    op.create_index("ix_threads_graph_id", "threads", ["graph_id"])
    op.add_column("crons", sa.Column("tenant_id", sa.String(length=128), nullable=True))
    op.add_column("crons", sa.Column("project_id", sa.String(length=128), nullable=True))
    op.create_index("ix_crons_scope_enabled", "crons", ["tenant_id", "project_id", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_crons_scope_enabled", table_name="crons")
    op.drop_column("crons", "project_id")
    op.drop_column("crons", "tenant_id")
    op.drop_index("ix_threads_graph_id", table_name="threads")
    op.drop_column("threads", "graph_id")
