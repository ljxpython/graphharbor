"""SQLAlchemy ORM models; schema DDL is applied via Alembic."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
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
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    event_seq: Mapped[int] = mapped_column(
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


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_status_created_at", "status", "created_at"),
        Index("ix_runs_thread_id_status", "thread_id", "status"),
        Index("ix_runs_thread_id_created_at", "thread_id", "created_at"),
        Index("ix_runs_assistant_id_status", "assistant_id", "status"),
        Index("ix_runs_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_runs_scope_status", "tenant_id", "project_id", "status"),
        Index(
            "uq_runs_scope_idempotency",
            "tenant_id",
            "project_id",
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
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=_JSONB_EMPTY
    )
    kwargs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=_JSONB_EMPTY)
    multitask_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )


class CronRow(Base):
    __tablename__ = "crons"
    __table_args__ = (
        Index("ix_crons_enabled_next_run", "enabled", "next_run_date"),
        Index("ix_crons_assistant_id", "assistant_id"),
        Index("ix_crons_thread_id", "thread_id"),
        Index("ix_crons_scope_enabled", "tenant_id", "project_id", "enabled"),
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
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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


class RuntimeSchemaRow(Base):
    """Application schema metadata, independent from Alembic's bookkeeping."""

    __tablename__ = "runtime_schema"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_NOW
    )
