"""Stable, editable ordering for pending runs."""

import sqlalchemy as sa
from alembic import op

revision = "010_run_queue_position"
down_revision = "009_event_retention_watermarks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("queue_position", sa.BigInteger(), nullable=True))
    op.execute("""WITH positions AS (
        SELECT run_id, row_number() OVER (
            PARTITION BY thread_id ORDER BY created_at, run_id
        ) AS position FROM runs
    ) UPDATE runs SET queue_position = positions.position
      FROM positions WHERE runs.run_id = positions.run_id""")


def downgrade() -> None:
    op.drop_column("runs", "queue_position")
