"""Transactional PostgreSQL run ownership primitives."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import RunLeaseRow, RunRow, RuntimeEventRow, ThreadRow
from langgraph_runtime_pg.observability import build_trace_metadata
from langgraph_runtime_pg.protocol import RunReason, RunStatus
from langgraph_runtime_pg.run_state import MAX_INFRASTRUCTURE_RETRIES, is_terminal, transition


class RunOwnershipError(RuntimeError):
    pass


class RunRepository:
    """The only code allowed to mutate worker ownership and terminal run state."""

    def __init__(
        self, *, lease_seconds: int = 60, max_attempts: int = MAX_INFRASTRUCTURE_RETRIES
    ) -> None:
        self.lease_seconds = max(lease_seconds, 5)
        self.max_attempts = max(1, min(max_attempts, MAX_INFRASTRUCTURE_RETRIES))
        try:
            self.retry_base_seconds = max(
                float(os.environ.get("GRAPHHARBOR_RETRY_BASE_SECONDS", "1")), 0.1
            )
        except ValueError:
            self.retry_base_seconds = 1.0
        self.last_requeued_events: list[RuntimeEventRow] = []
        self.last_transition_events: list[RuntimeEventRow] = []

    def retry_delay(self, retry_count: int) -> float:
        """Bounded exponential delay before the next infrastructure attempt."""
        return min(self.retry_base_seconds * (2 ** max(retry_count - 1, 0)), 30.0)

    async def create(
        self,
        session: Any,
        *,
        assistant_id: UUID,
        thread_id: UUID | None,
        kwargs: dict[str, Any],
        metadata: dict[str, Any] | None,
        tenant_id: str | None,
        project_id: str | None,
        idempotency_key: str | None = None,
        multitask_strategy: str | None = None,
    ) -> RunRow:
        """Create a pending run, returning the existing row for a repeated key."""
        if idempotency_key:
            existing = await session.scalar(
                select(RunRow).where(
                    RunRow.tenant_id == tenant_id,
                    RunRow.project_id == project_id,
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
        run = RunRow(
            assistant_id=assistant_id,
            thread_id=thread_id,
            tenant_id=tenant_id,
            project_id=project_id,
            status=RunStatus.PENDING.value,
            metadata_=dict(metadata or {}),
            kwargs=dict(kwargs),
            idempotency_key=idempotency_key,
            multitask_strategy=multitask_strategy,
            max_attempts=self.max_attempts,
        )
        try:
            # A pre-flight lookup is only an optimization. The unique index is
            # the authority when two API workers create the same idempotency key.
            async with session.begin_nested():
                session.add(run)
                await session.flush()
        except IntegrityError:
            if not idempotency_key:
                raise
            existing = await session.scalar(
                select(RunRow).where(
                    RunRow.tenant_id == tenant_id,
                    RunRow.project_id == project_id,
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            return existing
        return run

    async def claim_next(self, session: Any, owner: str) -> RunRow | None:
        self.last_transition_events = []
        now = datetime.now(UTC)
        running = aliased(RunRow)
        result = await session.execute(
            select(RunRow)
            .where(
                RunRow.status == RunStatus.PENDING.value,
                (RunRow.next_attempt_at.is_(None) | (RunRow.next_attempt_at <= now)),
                or_(
                    RunRow.thread_id.is_(None),
                    ~exists(
                        select(1).where(
                            running.thread_id == RunRow.thread_id,
                            running.status == RunStatus.RUNNING.value,
                        )
                    ),
                ),
            )
            .order_by(RunRow.created_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        run = result.scalar_one_or_none()
        if run is None:
            return None
        if run.retry_count >= run.max_attempts:
            run.status = RunStatus.ERROR.value
            run.reason = RunReason.INFRASTRUCTURE_ERROR.value
            run.next_attempt_at = None
            run.updated_at = now
            if run.thread_id is not None:
                thread = await session.scalar(
                    select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
                )
                if thread is not None:
                    thread.status = "idle"
            event = await self.record_event(
                session,
                run_id=run.run_id,
                thread_id=run.thread_id,
                topic="lifecycle",
                payload={
                    "event": "lifecycle",
                    "status": RunStatus.ERROR.value,
                    "reason": RunReason.INFRASTRUCTURE_ERROR.value,
                },
                terminal=True,
            )
            self.last_transition_events.append(event)
            await session.flush()
            return None
        if run.thread_id is not None:
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
            )
            if thread is not None:
                active = await session.scalar(
                    select(
                        exists(
                            select(1).where(
                                RunRow.thread_id == run.thread_id,
                                RunRow.status == RunStatus.RUNNING.value,
                                RunRow.run_id != run.run_id,
                            )
                        )
                    )
                )
                if active:
                    return None
                thread.status = "busy"
        run.status = RunStatus.RUNNING.value
        run.reason = None
        run.next_attempt_at = None
        run.retry_count += 1
        run.max_attempts = min(run.max_attempts or self.max_attempts, self.max_attempts)
        run.lease_owner = owner
        run.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        run.heartbeat_at = now
        await session.execute(
            insert(RunLeaseRow)
            .values(
                run_id=run.run_id,
                owner=owner,
                expires_at=run.lease_expires_at,
                heartbeat_at=now,
                generation=run.retry_count,
            )
            .on_conflict_do_update(
                index_elements=[RunLeaseRow.run_id],
                set_={
                    "owner": owner,
                    "expires_at": run.lease_expires_at,
                    "heartbeat_at": now,
                    "generation": run.retry_count,
                },
            )
        )
        await session.flush()
        metric_inc("graphharbor_lease_claims_total")
        return run

    async def renew(self, session: Any, run_id: UUID, owner: str) -> bool:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=self.lease_seconds)
        result = await session.execute(
            update(RunLeaseRow)
            .where(
                RunLeaseRow.run_id == run_id,
                RunLeaseRow.owner == owner,
                RunLeaseRow.expires_at > now,
            )
            .values(expires_at=expires, heartbeat_at=now)
        )
        if not result.rowcount:
            metric_inc("graphharbor_lease_renew_failures_total")
            return False
        await session.execute(
            update(RunRow)
            .where(RunRow.run_id == run_id, RunRow.lease_owner == owner)
            .values(lease_expires_at=expires, heartbeat_at=now, updated_at=now)
        )
        await session.flush()
        metric_inc("graphharbor_lease_renewals_total")
        return True

    async def finish(
        self,
        session: Any,
        run_id: UUID,
        owner: str,
        target: RunStatus | str,
        *,
        reason: RunReason | str,
        terminal_payload: dict[str, Any] | None = None,
        preceding_events: Sequence[tuple[str, dict[str, Any], list[str] | None]] = (),
        trace_context: dict[str, Any] | None = None,
    ) -> RunRow | None:
        self.last_transition_events = []
        now = datetime.now(UTC)
        result = await session.execute(
            select(RunRow).where(RunRow.run_id == run_id).with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RunOwnershipError(f"run {run_id} does not exist")
        if is_terminal(run.status):
            return None
        if run.lease_owner != owner:
            raise RunOwnershipError(f"run {run_id} is owned by another worker")
        if terminal_payload is not None and not is_terminal(target):
            raise ValueError("terminal_payload requires a terminal run status")
        if terminal_payload is None and is_terminal(target):
            terminal_payload = {
                "event": "lifecycle",
                "status": str(target),
                "reason": str(reason),
            }
        for topic, payload, namespace in preceding_events:
            event = await self.record_event(
                session,
                run_id=run_id,
                thread_id=run.thread_id,
                topic=topic,
                payload=payload,
                namespace=namespace,
                trace_context=trace_context,
            )
            self.last_transition_events.append(event)
        change = transition(run.status, target, reason=reason, retry_count=run.retry_count)
        run.status = change.status.value
        run.reason = change.reason.value
        run.lease_owner = None
        run.lease_expires_at = None
        run.next_attempt_at = None
        run.heartbeat_at = now
        run.updated_at = now
        await session.execute(delete(RunLeaseRow).where(RunLeaseRow.run_id == run_id))
        if terminal_payload is not None:
            event = await self.record_event(
                session,
                run_id=run_id,
                thread_id=run.thread_id,
                topic="lifecycle",
                payload=terminal_payload,
                terminal=True,
                trace_context=trace_context,
            )
            self.last_transition_events.append(event)
        await session.flush()
        return run

    async def fail(
        self,
        session: Any,
        run_id: UUID,
        owner: str,
        *,
        infrastructure: bool,
        reason: RunReason | str,
        terminal_payload: dict[str, Any] | None = None,
        trace_context: dict[str, Any] | None = None,
    ) -> RunRow:
        """Release a failed claim; only infrastructure failures are requeued."""
        self.last_transition_events = []
        now = datetime.now(UTC)
        result = await session.execute(
            select(RunRow).where(RunRow.run_id == run_id).with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RunOwnershipError(f"run {run_id} does not exist")
        if run.lease_owner != owner:
            raise RunOwnershipError(f"run {run_id} is owned by another worker")
        if infrastructure and run.retry_count < min(run.max_attempts, self.max_attempts):
            change = transition(
                run.status,
                RunStatus.PENDING,
                reason=RunReason.RETRY,
                retry_count=run.retry_count,
            )
            run.next_attempt_at = now + timedelta(seconds=self.retry_delay(run.retry_count))
            metric_inc("graphharbor_run_retries_total")
        else:
            change = transition(
                run.status,
                RunStatus.ERROR,
                reason=reason,
                retry_count=run.retry_count,
            )
            run.next_attempt_at = None
            metric_inc("graphharbor_run_retry_exhausted_total")
        run.status = change.status.value
        run.reason = change.reason.value
        run.lease_owner = None
        run.lease_expires_at = None
        run.heartbeat_at = now
        run.updated_at = now
        await session.execute(delete(RunLeaseRow).where(RunLeaseRow.run_id == run_id))
        payload = terminal_payload or {
            "event": "lifecycle",
            "status": run.status,
            "reason": run.reason,
        }
        event = await self.record_event(
            session,
            run_id=run_id,
            thread_id=run.thread_id,
            topic="lifecycle",
            payload=payload,
            terminal=is_terminal(run.status),
            trace_context=trace_context,
        )
        self.last_transition_events.append(event)
        await session.flush()
        return run

    async def requeue_for_shutdown(self, session: Any, run_id: UUID, owner: str) -> RunRow:
        """Release a claimed run for graceful worker shutdown."""
        self.last_transition_events = []
        now = datetime.now(UTC)
        result = await session.execute(
            select(RunRow).where(RunRow.run_id == run_id).with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RunOwnershipError(f"run {run_id} does not exist")
        if run.lease_owner != owner:
            raise RunOwnershipError(f"run {run_id} is owned by another worker")
        if run.status != RunStatus.RUNNING.value:
            return run
        change = transition(
            run.status,
            RunStatus.PENDING,
            reason=RunReason.SHUTDOWN_REQUEUE,
            retry_count=run.retry_count,
        )
        run.status = change.status.value
        run.reason = change.reason.value
        run.next_attempt_at = now + timedelta(seconds=self.retry_delay(run.retry_count))
        run.lease_owner = None
        run.lease_expires_at = None
        run.heartbeat_at = now
        run.updated_at = now
        if run.thread_id is not None:
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
            )
            if thread is not None:
                thread.status = "idle"
        await session.execute(delete(RunLeaseRow).where(RunLeaseRow.run_id == run_id))
        self.last_transition_events.append(
            await self.record_event(
                session,
                run_id=run_id,
                thread_id=run.thread_id,
                topic="lifecycle",
                payload={
                    "event": "lifecycle",
                    "status": run.status,
                    "reason": run.reason,
                },
            )
        )
        await session.flush()
        metric_inc("graphharbor_shutdown_requeues_total")
        return run

    async def record_event(
        self,
        session: Any,
        *,
        run_id: UUID | None,
        thread_id: UUID | None,
        topic: str,
        payload: dict[str, Any],
        namespace: list[str] | None = None,
        trace_context: dict[str, Any] | None = None,
        terminal: bool = False,
    ) -> RuntimeEventRow:
        """Append a durable, monotonic event cursor in the same transaction as the run."""
        if run_id is not None:
            run = await session.scalar(
                select(RunRow).where(RunRow.run_id == run_id).with_for_update()
            )
            if run is None:
                raise RunOwnershipError(f"run {run_id} does not exist")
            if thread_id is not None and thread_id != run.thread_id:
                raise ValueError("event thread_id does not match the run thread")
            thread_id = run.thread_id
            if terminal:
                existing = await session.scalar(
                    select(RuntimeEventRow).where(
                        RuntimeEventRow.run_id == run_id,
                        RuntimeEventRow.terminal.is_(True),
                    )
                )
                if existing is not None:
                    return existing
            if run.thread_id is not None:
                thread = await session.scalar(
                    select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
                )
                if thread is None:
                    raise RunOwnershipError(f"thread {run.thread_id} does not exist")
                max_sequence = int(
                    await session.scalar(
                        select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
                            RuntimeEventRow.thread_id == run.thread_id
                        )
                    )
                    or 0
                )
                sequence = max(thread.event_seq, max_sequence) + 1
                thread.event_seq = sequence
            else:
                max_sequence = int(
                    await session.scalar(
                        select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
                            RuntimeEventRow.run_id == run.run_id
                        )
                    )
                    or 0
                )
                sequence = max(run.event_seq, max_sequence) + 1
            run.event_seq = max(run.event_seq, sequence)
        else:
            if thread_id is None:
                raise ValueError("thread_id is required when recording a runless event")
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == thread_id).with_for_update()
            )
            if thread is None:
                raise RunOwnershipError(f"thread {thread_id} does not exist")
            max_sequence = int(
                await session.scalar(
                    select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
                        RuntimeEventRow.thread_id == thread_id
                    )
                )
                or 0
            )
            sequence = max(thread.event_seq, max_sequence) + 1
            thread.event_seq = sequence
        event_payload = dict(payload)
        event_payload["trace"] = build_trace_metadata(
            event={"event": topic, "namespace": namespace or [], **payload},
            context={
                "run_id": str(run_id) if run_id is not None else None,
                "thread_id": str(thread_id) if thread_id is not None else None,
                **(trace_context or {}),
            },
        )
        event = RuntimeEventRow(
            run_id=run_id,
            thread_id=thread_id,
            sequence=sequence,
            topic=topic,
            namespace=list(namespace or []),
            payload=event_payload,
            terminal=terminal,
        )
        session.add(event)
        await session.flush()
        return event

    async def requeue_expired(self, session: Any, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        self.last_requeued_events = []
        self.last_transition_events = []
        result = await session.execute(
            select(RunRow)
            .where(RunRow.status == RunStatus.RUNNING.value, RunRow.lease_expires_at < now)
            .with_for_update(skip_locked=True)
        )
        runs = list(result.scalars())
        count = 0
        for run in runs:
            if run.retry_count >= run.max_attempts:
                change = transition(
                    run.status,
                    RunStatus.ERROR,
                    reason=RunReason.INFRASTRUCTURE_ERROR,
                    retry_count=run.retry_count,
                )
                run.status = change.status.value
                run.reason = change.reason.value
                run.next_attempt_at = None
            else:
                change = transition(
                    run.status,
                    RunStatus.PENDING,
                    reason=RunReason.SHUTDOWN_REQUEUE,
                    retry_count=run.retry_count,
                )
                run.status = change.status.value
                run.reason = change.reason.value
                run.next_attempt_at = now + timedelta(seconds=self.retry_delay(run.retry_count))
            run.lease_owner = None
            run.lease_expires_at = None
            run.updated_at = now
            if run.thread_id is not None:
                thread = await session.scalar(
                    select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
                )
                if thread is not None:
                    thread.status = "idle"
                    if run.status == RunStatus.ERROR.value:
                        thread.error = {"reason": RunReason.INFRASTRUCTURE_ERROR.value}
            self.last_requeued_events.append(
                await self.record_event(
                    session,
                    run_id=run.run_id,
                    thread_id=run.thread_id,
                    topic="lifecycle",
                    payload={
                        "event": "lifecycle",
                        "status": run.status,
                        "reason": run.reason,
                    },
                    namespace=[],
                    terminal=run.status == RunStatus.ERROR.value,
                )
            )
            count += 1
        if count:
            await session.execute(
                delete(RunLeaseRow).where(RunLeaseRow.run_id.in_([run.run_id for run in runs]))
            )
            await session.flush()
            metric_inc("graphharbor_lease_reclaims_total", count)
        return count
