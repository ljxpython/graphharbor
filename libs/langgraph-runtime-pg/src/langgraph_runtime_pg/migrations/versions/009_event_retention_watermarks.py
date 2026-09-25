"""Record deleted event ranges for explicit replay-gap detection."""

import sqlalchemy as sa
from alembic import op

revision = "009_event_retention_watermarks"
down_revision = "008_remove_business_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("threads", "runs"):
        op.add_column(
            table,
            sa.Column(
                "event_pruned_through",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    for table in ("runs", "threads"):
        op.drop_column(table, "event_pruned_through")
