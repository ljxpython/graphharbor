"""Durable Run acceptance and permanent retry tombstones."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "013_run_acceptance_receipts"
down_revision = "012_run_callback_deliveries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_acceptance_receipts",
        sa.Column("receipt_key", sa.String(64), primary_key=True),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column("credential_id", sa.String(64), nullable=False),
        sa.Column("key_sha256", sa.String(64), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("request_digest", sa.String(71), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(32), nullable=True),
        sa.Column("authorization_metadata", JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "scope_id", "credential_id", "key_sha256", name="uq_run_acceptance_binding"
        ),
        sa.CheckConstraint(
            "(outcome = 'accepted' AND run_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND rejection_reason IS NULL AND authorization_metadata IS NOT NULL "
            "AND ((closed_observed_at IS NULL AND details_expires_at IS NULL) OR "
            "(closed_observed_at IS NOT NULL AND details_expires_at IS NOT NULL))) OR "
            "(outcome = 'rejected' AND run_id IS NULL AND accepted_at IS NULL "
            "AND rejection_reason IS NOT NULL AND rejection_reason = 'multitask_rejected' "
            "AND authorization_metadata IS NOT NULL AND details_expires_at IS NOT NULL) OR "
            "(outcome = 'expired' AND run_id IS NULL AND accepted_at IS NULL "
            "AND rejection_reason IS NULL AND authorization_metadata IS NULL "
            "AND details_expires_at IS NOT NULL)",
            name="ck_run_acceptance_outcome",
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_acceptance_open",
        "run_acceptance_receipts",
        ["recorded_at"],
        postgresql_where=sa.text("outcome = 'accepted' AND closed_observed_at IS NULL"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_run_acceptance_expiry",
        "run_acceptance_receipts",
        ["details_expires_at"],
        postgresql_where=sa.text("outcome <> 'expired' AND details_expires_at IS NOT NULL"),
        if_not_exists=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "Retain acceptance receipts and tombstones when rolling back application code"
    )
