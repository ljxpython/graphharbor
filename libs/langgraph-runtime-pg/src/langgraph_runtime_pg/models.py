"""SQLAlchemy ORM models; schema DDL is applied via Alembic."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

_JSONB_EMPTY = text("'{}'::jsonb")
_NOW = text("now()")


class Base(DeclarativeBase):
    pass


class AssistantRow(Base):
    __tablename__ = "assistants"
    __table_args__ = (
        Index("ix_assistants_graph_id", "graph_id"),
        Index("ix_assistants_created_at", "created_at"),
        Index(
            "ix_assistants_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    graph_id: Mapped[str] = mapped_column(String(256), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class AssistantVersionRow(Base):
    __tablename__ = "assistant_versions"

    assistant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    graph_id: Mapped[str] = mapped_column(String(256), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class ThreadRow(Base):
    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_threads_graph_id", "graph_id"),
        Index("ix_threads_status_updated_at", "status", "updated_at"),
        Index("ix_threads_updated_at", "updated_at"),
        Index("ix_threads_expires_at", "expires_at"),
        Index(
            "ix_threads_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )

    thread_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    graph_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    event_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    event_pruned_through: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    values_: Mapped[dict | None] = mapped_column("values", JSONB, nullable=True)
    interrupts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    state_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retention: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True), nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ThreadDeletionRow(Base):
    """Durable handoff and tombstone, deliberately independent of the thread."""

    __tablename__ = "thread_deletions"
    __table_args__ = (Index("ix_thread_deletions_due", "state", "next_attempt_at"),)

    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), unique=True, default=uuid.uuid4
    )
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="before_delete")
    hooks: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(256))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=_NOW)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_thread_id_status", "thread_id", "status"),
        Index("ix_runs_thread_id_created_at", "thread_id", "created_at"),
        Index("ix_runs_assistant_id_status", "assistant_id", "status"),
        Index("ix_runs_next_attempt_at", "status", "next_attempt_at"),
        Index(
            "uq_runs_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
        Index(
            "uq_runs_one_running_per_thread",
            "thread_id",
            unique=True,
            postgresql_where=text("status = 'running' AND thread_id IS NOT NULL"),
        ),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    assistant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default=text("3")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    event_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    event_pruned_through: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    kwargs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    queue_position: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    multitask_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class RunAcceptanceRow(Base):
    """Permanent retry binding, independent of Run/Thread deletion."""

    __tablename__ = "run_acceptance_receipts"
    __table_args__ = (
        UniqueConstraint(
            "scope_id", "credential_id", "key_sha256", name="uq_run_acceptance_binding"
        ),
        CheckConstraint(
            "(outcome = 'accepted' AND run_id IS NOT NULL AND accepted_at IS NOT NULL "
            "AND rejection_reason IS NULL AND authorization_metadata IS NOT NULL "
            "AND ((closed_observed_at IS NULL AND details_expires_at IS NULL) OR "
            "(closed_observed_at IS NOT NULL AND details_expires_at IS NOT NULL))) OR "
            "(outcome = 'rejected' AND run_id IS NULL AND accepted_at IS NULL "
            "AND rejection_reason IS NOT NULL AND rejection_reason = 'multitask_rejected' AND authorization_metadata IS NOT NULL "
            "AND details_expires_at IS NOT NULL) OR "
            "(outcome = 'expired' AND run_id IS NULL AND accepted_at IS NULL "
            "AND rejection_reason IS NULL AND authorization_metadata IS NULL "
            "AND details_expires_at IS NOT NULL)",
            name="ck_run_acceptance_outcome",
        ),
        Index(
            "ix_run_acceptance_open",
            "recorded_at",
            postgresql_where=text("outcome = 'accepted' AND closed_observed_at IS NULL"),
        ),
        Index(
            "ix_run_acceptance_expiry",
            "details_expires_at",
            postgresql_where=text("outcome <> 'expired' AND details_expires_at IS NOT NULL"),
        ),
    )

    receipt_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    credential_id: Mapped[str] = mapped_column(String(64), nullable=False)
    key_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authorization_metadata: Mapped[dict | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    closed_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    details_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RunCancellationRow(Base):
    """A fixed cancellation boundary, independent of subsequent submissions."""

    __tablename__ = "run_cancellations"
    __table_args__ = (UniqueConstraint("thread_id", "principal", "idempotency_key"),)

    cancellation_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    principal: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    targets: Mapped[list] = mapped_column(JSONB, nullable=False)
    reconcile_run_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    has_pending_interrupts: Mapped[bool] = mapped_column(Boolean, nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunCheckpointBaselineRow(Base):
    """Small run boundary; touched namespaces are captured lazily by the saver."""

    __tablename__ = "run_checkpoint_baselines"
    __table_args__ = (Index("ix_run_checkpoint_boundaries_order", "thread_id", "sequence"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("threads.thread_id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    heads: Mapped[dict] = mapped_column(JSONB, nullable=False)
    projection: Mapped[dict] = mapped_column(JSONB, nullable=False)


class RunCheckpointUndoRow(Base):
    """First before-image for each key actually changed by a run."""

    __tablename__ = "run_checkpoint_undo"

    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("run_checkpoint_baselines.run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    table_name: Mapped[str] = mapped_column(Text, primary_key=True)
    row_key: Mapped[dict] = mapped_column(JSONB, primary_key=True)
    before_image: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))


class CronRow(Base):
    __tablename__ = "crons"
    __table_args__ = (
        Index("ix_crons_enabled_next_run", "enabled", "next_run_date"),
        Index("ix_crons_assistant_id", "assistant_id"),
        Index("ix_crons_thread_id", "thread_id"),
        Index(
            "ix_crons_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )

    cron_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    assistant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    schedule: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    next_run_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    on_run_completed: Mapped[str | None] = mapped_column(String(16), nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class StoreItemRow(Base):
    __tablename__ = "store_items"

    prefix: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetryCounterRow(Base):
    __tablename__ = "retry_counters"

    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RunLeaseRow(Base):
    """Durable worker lease; Redis heartbeats are only a transport hint."""

    __tablename__ = "run_leases"
    __table_args__ = (Index("ix_run_leases_expires_at", "expires_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    owner: Mapped[str] = mapped_column(String(256), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )


class RuntimeEventRow(Base):
    """Durable event cursor used when the bounded Redis replay buffer is gone."""

    __tablename__ = "runtime_events"
    __table_args__ = (
        Index("ix_runtime_events_thread_seq", "thread_id", "sequence"),
        Index("ix_runtime_events_created_at", "created_at"),
        Index(
            "uq_runtime_events_run_terminal",
            "run_id",
            unique=True,
            postgresql_where=text("terminal = true AND run_id IS NOT NULL"),
        ),
        UniqueConstraint("run_id", "sequence", name="uq_runtime_events_run_sequence"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.run_id", ondelete="CASCADE"), nullable=True
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("threads.thread_id", ondelete="CASCADE"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    namespace: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    terminal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class RunCallbackDeliveryRow(Base):
    """Independent snapshot retained after Run, Thread and terminal event deletion."""

    __tablename__ = "run_callback_deliveries"
    __table_args__ = (
        UniqueConstraint("run_id", "profile", name="uq_run_callback_profile"),
        Index("ix_run_callback_due", "state", "next_attempt_at"),
    )

    delivery_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    profile: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retry_deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeSchemaRow(Base):
    """Application schema metadata, independent from Alembic's bookkeeping."""

    __tablename__ = "runtime_schema"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
