"""Transactional PostgreSQL run ownership primitives."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, case, delete, exists, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from langgraph_runtime_pg.checkpoint_mutations import capture_baseline, checkpoint_writer
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import (
    AssistantRow,
    RetryCounterRow,
    RunLeaseRow,
    RunRow,
    RuntimeEventRow,
    ThreadRow,
)
from langgraph_runtime_pg.observability import build_trace_metadata
from langgraph_runtime_pg.protocol import RunReason, RunStatus
from langgraph_runtime_pg.run_state import MAX_INFRASTRUCTURE_RETRIES, is_terminal, transition


class RunOwnershipError(RuntimeError):
    pass


class RunConflictError(ValueError):
    """A run submission conflicts with a request or active execution."""

    def __init__(self, message: str, *, reason: str = "target_conflict") -> None:
        super().__init__(message)
        self.reason = reason


def is_resume(kwargs: dict) -> bool:
    command = kwargs.get("command")
    return isinstance(command, dict) and "resume" in command


async def stopped_event(session: Any, run: RunRow) -> RuntimeEventRow | None:
    """A public terminal status alone does not acknowledge execution shutdown."""
    if not is_terminal(run.status) or run.lease_owner is not None:
        return None
    event = await session.scalar(
        select(RuntimeEventRow).where(
            RuntimeEventRow.run_id == run.run_id, RuntimeEventRow.terminal.is_(True)
        )
    )
    if event is None or event.payload.get("execution_stopped") is False:
        return None
    if run.reason in {RunReason.CANCEL_REQUESTED.value, RunReason.ROLLBACK.value}:
        return event if event.payload.get("execution_stopped") is True else None
    return event


class RunRepository:
    """The only code allowed to mutate worker ownership and terminal run state."""

    def __init__(
        self,
        *,
        lease_seconds: int = 60,
        max_attempts: int | None = None,
        run_timeout_seconds: float | None = None,
    ) -> None:
        self.lease_seconds = max(lease_seconds, 5)
        if max_attempts is None:
            max_attempts = int(os.environ.get("BG_JOB_MAX_RETRIES", MAX_INFRASTRUCTURE_RETRIES))
        if max_attempts < 1:
            raise ValueError("BG_JOB_MAX_RETRIES must be a positive integer")
        self.max_attempts = max_attempts
        self.run_timeout_seconds = run_timeout_seconds
        try:
            self.retry_base_seconds = max(
                float(os.environ.get("GRAPHHARBOR_RETRY_BASE_SECONDS", "1")), 0.1
            )
        except ValueError:
            self.retry_base_seconds = 1.0
        self.last_requeued_events: list[RuntimeEventRow] = []
        self.last_transition_events: list[RuntimeEventRow] = []

    async def attempt_count(self, session: Any, run: RunRow) -> int:
        count = await session.scalar(
            select(RetryCounterRow.count).where(RetryCounterRow.run_id == run.run_id)
        )
        return int(count) if count is not None else run.retry_count

    async def can_retry(self, session: Any, run: RunRow) -> bool:
        return await self.attempt_count(session, run) < min(run.max_attempts, self.max_attempts)

    def retry_delay(self, retry_count: int) -> float:
        """Bounded exponential delay before the next infrastructure attempt."""
        return min(self.retry_base_seconds * (2 ** max(retry_count - 1, 0)), 30.0)

    async def _assert_owner(
        self, session: Any, run: RunRow, owner: str, generation: int | None
    ) -> None:
        lease = await session.get(RunLeaseRow, run.run_id)
        if (
            run.lease_owner != owner
            or lease is None
            or lease.owner != owner
            or lease.generation != run.retry_count
            or (generation is not None and lease.generation != generation)
            or lease.expires_at <= datetime.now(UTC)
        ):
            raise RunOwnershipError(f"run {run.run_id} is owned by another worker")

    async def _assert_event_writer(self, session: Any, run: RunRow) -> None:
        writer = checkpoint_writer.get()
        if writer is not None:
            if str(run.run_id) != writer[0]:
                raise RunOwnershipError("event writer targets another run")
            await self._assert_owner(session, run, writer[1], writer[2])

    async def search(
        self, session: Any, *, metadata: dict[str, Any], limit: int, offset: int
    ) -> tuple[list[RunRow], int]:
        """Filter before pagination; native rows remain the execution source of truth."""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid run pagination")
        query = select(RunRow).where(RunRow.metadata_.contains(metadata))
        total = await session.scalar(select(func.count()).select_from(query.subquery()))
        rows = (
            await session.scalars(
                query.order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return list(rows), int(total or 0)

    async def create(
        self,
        session: Any,
        *,
        assistant_id: UUID,
        thread_id: UUID | None,
        kwargs: dict[str, Any],
        metadata: dict[str, Any] | None,
        idempotency_key: str | None = None,
        multitask_strategy: str | None = None,
    ) -> RunRow:
        """Create a pending run, returning the existing row for a repeated key."""
        if thread_id is not None:
            # Serialize submissions on this thread across API processes. Enqueue
            # submissions take the same lock so reject cannot race past them.
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == thread_id).with_for_update()
            )
            from langgraph_runtime_pg.models import ThreadDeletionRow

            if (
                thread is not None and thread.deletion_requested_at is not None
            ) or await session.get(ThreadDeletionRow, thread_id) is not None:
                raise RunConflictError("Thread deletion has been requested")
        if idempotency_key:
            existing = await session.scalar(
                select(RunRow).where(
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                if existing.thread_id != thread_id or existing.assistant_id != assistant_id:
                    raise RunConflictError("Idempotency key belongs to another run target")
                return existing
        if thread_id is not None and multitask_strategy == "reject" and not is_resume(kwargs):
            active = await session.scalar(
                select(RunRow.run_id)
                .where(
                    RunRow.thread_id == thread_id,
                    RunRow.status.in_((RunStatus.PENDING.value, RunStatus.RUNNING.value)),
                )
                .limit(1)
            )
            if active is not None:
                raise RunConflictError(
                    "Thread already has a pending or running run", reason="multitask_rejected"
                )
        position = await session.scalar(
            select(func.coalesce(func.max(RunRow.queue_position), 0) + 1).where(
                RunRow.thread_id == thread_id
            )
        )
        run = RunRow(
            queue_position=position,
            assistant_id=assistant_id,
            thread_id=thread_id,
            status=RunStatus.PENDING.value,
            metadata_=dict(metadata or {}),
            kwargs={
                key: value
                for key, value in kwargs.items()
                if key not in {"__graphharbor_resume_after_drain", "__graphharbor_run_budget"}
            },
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
                    RunRow.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            if existing.thread_id != thread_id or existing.assistant_id != assistant_id:
                raise RunConflictError("Idempotency key belongs to another run target") from None
            return existing
        return run

    async def claim_next(self, session: Any, owner: str) -> RunRow | None:
        self.last_transition_events = []
        now = await session.scalar(select(func.clock_timestamp()))
        running = aliased(RunRow)
        earlier = aliased(RunRow)
        thread_scope = aliased(ThreadRow)
        resume = func.coalesce(RunRow.kwargs["command"].has_key("resume"), False)
        earlier_resume = func.coalesce(earlier.kwargs["command"].has_key("resume"), False)
        rank = case((resume, 0), else_=1)
        earlier_rank = case((earlier_resume, 0), else_=1)
        # A queued retry remains the head even during its backoff. SKIP LOCKED
        # must never let another worker jump over a locked head of this thread.
        eligible = (
            RunRow.status == RunStatus.PENDING.value,
            (RunRow.next_attempt_at.is_(None) | (RunRow.next_attempt_at <= now)),
            or_(
                RunRow.thread_id.is_(None),
                and_(
                    ~exists(
                        select(1).where(
                            running.thread_id == RunRow.thread_id,
                            or_(
                                running.status == "running",
                                running.reason == "rollback",
                                and_(
                                    running.lease_owner.is_not(None), running.lease_expires_at > now
                                ),
                            ),
                        )
                    ),
                    ~exists(
                        select(1).where(
                            thread_scope.thread_id == RunRow.thread_id,
                            thread_scope.interrupts != {},
                            ~resume,
                        )
                    ),
                    ~exists(
                        select(1).where(
                            earlier.thread_id == RunRow.thread_id,
                            earlier.status == "pending",
                            earlier.run_id != RunRow.run_id,
                            tuple_(
                                earlier_rank,
                                func.coalesce(earlier.queue_position, 0),
                                earlier.created_at,
                                earlier.run_id,
                            )
                            < tuple_(
                                rank,
                                func.coalesce(RunRow.queue_position, 0),
                                RunRow.created_at,
                                RunRow.run_id,
                            ),
                        )
                    ),
                ),
            ),
        )
        run = await session.scalar(
            select(RunRow)
            .where(*eligible)
            .order_by(RunRow.created_at, RunRow.run_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            return None
        # Existing event/lease writers lock run then thread. Never wait for a
        # thread held by a submitter; release this claim and retry next tick.
        if run.thread_id is not None:
            thread = await session.scalar(
                select(ThreadRow)
                .where(ThreadRow.thread_id == run.thread_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if thread is None:
                if await session.get(ThreadRow, run.thread_id) is None:
                    # SKIP LOCKED can also mean the Thread was permanently deleted.
                    run.status = RunStatus.ERROR.value
                    run.reason = RunReason.BUSINESS_ERROR.value
                    run.metadata_ = {**run.metadata_, "error_code": "thread_not_found"}
                    run.updated_at = now
                    run.next_attempt_at = None
                    self.last_transition_events.append(
                        await self.record_event(
                            session,
                            run_id=run.run_id,
                            thread_id=run.thread_id,
                            topic="lifecycle",
                            payload={
                                "event": "lifecycle",
                                "status": "error",
                                "reason": run.reason,
                                "error_code": "thread_not_found",
                            },
                            terminal=True,
                        )
                    )
                    await session.flush()
                return None
            if thread.interrupts and not is_resume(run.kwargs):
                return None
            if (
                await session.scalar(
                    select(RunRow.run_id).where(RunRow.run_id == run.run_id, *eligible)
                )
                is None
            ):
                return None
        attempt = await self.attempt_count(session, run)
        if attempt >= min(run.max_attempts, self.max_attempts):
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
                                or_(
                                    RunRow.status == RunStatus.RUNNING.value,
                                    RunRow.reason == "rollback",
                                ),
                                RunRow.run_id != run.run_id,
                            )
                        )
                    )
                )
                if active:
                    return None
                await capture_baseline(session, run, thread)
                thread.status = "busy"
        else:
            await capture_baseline(session, run, None)
        if self.run_timeout_seconds is not None:
            started_at = await session.scalar(select(func.clock_timestamp()))
            run.kwargs = {
                **run.kwargs,
                "__graphharbor_run_budget": {
                    "version": 1,
                    "started_at": started_at.isoformat(),
                    "deadline_at": (
                        started_at + timedelta(seconds=self.run_timeout_seconds)
                    ).isoformat(),
                    "timeout_seconds": self.run_timeout_seconds,
                },
            }
        run.status = RunStatus.RUNNING.value
        run.reason = None
        run.next_attempt_at = None
        run.retry_count += 1
        run.max_attempts = min(run.max_attempts or self.max_attempts, self.max_attempts)
        # Keep fencing generations increasing even when a drained handoff refunds an attempt.
        await session.execute(
            insert(RetryCounterRow)
            .values(run_id=run.run_id, count=attempt + 1)
            .on_conflict_do_update(
                index_elements=[RetryCounterRow.run_id], set_={"count": attempt + 1}
            )
        )
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

    async def renew(
        self, session: Any, run_id: UUID, owner: str, *, generation: int | None = None
    ) -> bool:
        now = datetime.now(UTC)
        run = await session.scalar(select(RunRow).where(RunRow.run_id == run_id).with_for_update())
        if (
            run is None
            or run.lease_owner != owner
            or (generation is not None and run.retry_count != generation)
            or (
                run.status != "running"
                and run.reason not in {RunReason.CANCEL_REQUESTED.value, RunReason.ROLLBACK.value}
            )
        ):
            return False
        expires = now + timedelta(seconds=self.lease_seconds)
        result = await session.execute(
            update(RunLeaseRow)
            .where(
                RunLeaseRow.run_id == run_id,
                RunLeaseRow.owner == owner,
                RunLeaseRow.generation == run.retry_count,
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
        generation: int | None = None,
    ) -> RunRow | None:
        self.last_transition_events = []
        now = datetime.now(UTC)
        result = await session.execute(
            select(RunRow).where(RunRow.run_id == run_id).with_for_update()
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RunOwnershipError(f"run {run_id} does not exist")
        cancel_pending = run.reason == RunReason.CANCEL_REQUESTED.value
        if is_terminal(run.status) and (not cancel_pending or run.lease_owner is None):
            return None
        await self._assert_owner(session, run, owner, generation)
        if cancel_pending:
            target, reason = RunStatus.INTERRUPTED, RunReason.CANCEL_REQUESTED
            terminal_payload = {"event": "lifecycle", "status": str(target), "reason": str(reason)}
            preceding_events = ()
        if terminal_payload is not None and not is_terminal(target):
            raise ValueError("terminal_payload requires a terminal run status")
        if terminal_payload is None and is_terminal(target):
            terminal_payload = {
                "event": "lifecycle",
                "status": str(target),
                "reason": str(reason),
            }
        if terminal_payload is not None:
            terminal_payload = {**terminal_payload, "execution_stopped": True}
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
        if str(reason) == RunReason.CANCEL_REQUESTED.value and run.thread_id is not None:
            thread = await session.scalar(
                select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
            )
            if thread is not None:
                thread.status = "interrupted" if thread.interrupts else "idle"
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

    async def confirm_fenced_stop(
        self, session: Any, run: RunRow, generation: int
    ) -> RuntimeEventRow | None:
        if (
            run.retry_count != generation
            or not is_terminal(run.status)
            or run.lease_owner is not None
        ):
            return None
        event = await session.scalar(
            select(RuntimeEventRow).where(
                RuntimeEventRow.run_id == run.run_id, RuntimeEventRow.terminal.is_(True)
            )
        )
        if (
            event is None
            or event.payload.get("lease_fenced") is not True
            or event.payload.get("generation") != generation
        ):
            return None
        return await self.record_event(
            session,
            run_id=run.run_id,
            thread_id=run.thread_id,
            topic="lifecycle",
            payload={"execution_stopped": True},
            terminal=True,
        )

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
        generation: int | None = None,
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
        if is_terminal(run.status):
            return run
        await self._assert_owner(session, run, owner, generation)
        if infrastructure and await self.can_retry(session, run):
            change = transition(
                run.status,
                RunStatus.PENDING,
                reason=RunReason.RETRY,
                retry_count=run.retry_count,
            )
            attempt = await self.attempt_count(session, run)
            run.next_attempt_at = now + timedelta(seconds=self.retry_delay(attempt))
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

    async def requeue_for_shutdown(
        self,
        session: Any,
        run_id: UUID,
        owner: str,
        *,
        drained: bool = False,
        generation: int | None = None,
    ) -> RunRow:
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
        await self._assert_owner(session, run, owner, generation)
        change = transition(
            run.status,
            RunStatus.PENDING,
            reason=RunReason.SHUTDOWN_REQUEUE,
            retry_count=run.retry_count,
        )
        run.status = change.status.value
        run.reason = change.reason.value
        if drained:
            run.kwargs = {**run.kwargs, "__graphharbor_resume_after_drain": True}
            attempt = await self.attempt_count(session, run)
            await session.execute(
                insert(RetryCounterRow)
                .values(run_id=run_id, count=max(0, attempt - 1))
                .on_conflict_do_update(
                    index_elements=[RetryCounterRow.run_id], set_={"count": max(0, attempt - 1)}
                )
            )
        run.next_attempt_at = (
            None
            if drained
            else now + timedelta(seconds=self.retry_delay(await self.attempt_count(session, run)))
        )
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
        event_thread_id = thread_id
        if run_id is not None:
            run = await session.scalar(
                select(RunRow).where(RunRow.run_id == run_id).with_for_update()
            )
            if run is None:
                raise RunOwnershipError(f"run {run_id} does not exist")
            await self._assert_event_writer(session, run)
            if thread_id is not None and thread_id != run.thread_id:
                raise ValueError("event thread_id does not match the run thread")
            thread_id = run.thread_id
            event_thread_id = thread_id
            if topic == "lifecycle" and payload.get("status") and "graph_name" not in payload:
                assistant = await session.get(AssistantRow, run.assistant_id)
                payload = {
                    **payload,
                    "graph_name": assistant.graph_id if assistant else str(run.assistant_id),
                }
            if terminal:
                existing = await session.scalar(
                    select(RuntimeEventRow).where(
                        RuntimeEventRow.run_id == run_id,
                        RuntimeEventRow.terminal.is_(True),
                    )
                )
                if existing is not None:
                    from langgraph_runtime_pg.webhooks import snapshot_deliveries

                    if (
                        existing.payload.get("execution_stopped") is False
                        and payload.get("execution_stopped") is True
                    ):
                        existing.payload = {**existing.payload, "execution_stopped": True}
                        existing.created_at = datetime.now(UTC)
                        await session.flush()
                    await snapshot_deliveries(session, run, existing)
                    return existing
            if run.thread_id is not None:
                thread = await session.scalar(
                    select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
                )
                if thread is None:
                    if not terminal:
                        raise RunOwnershipError(f"thread {run.thread_id} does not exist")
                    # Retain the original Thread in trace; its deleted FK cannot
                    # own a new event. The locked Run owns this terminal cursor.
                    event_thread_id = None
                max_sequence = int(
                    await session.scalar(
                        select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
                            RuntimeEventRow.thread_id == run.thread_id
                            if thread is not None
                            else RuntimeEventRow.run_id == run.run_id
                        )
                    )
                    or 0
                )
                sequence = max(thread.event_seq if thread else run.event_seq, max_sequence) + 1
                if thread is not None:
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
        event_payload.setdefault("timestamp", int(datetime.now(UTC).timestamp() * 1000))
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
            thread_id=event_thread_id,
            sequence=sequence,
            topic=topic,
            namespace=list(namespace or []),
            payload=event_payload,
            terminal=terminal,
        )
        session.add(event)
        await session.flush()
        if terminal and run_id is not None:
            from langgraph_runtime_pg.webhooks import snapshot_deliveries

            await snapshot_deliveries(
                session,
                run,
                event,
                awaiting_stop=event.payload.get("lease_fenced") is True
                and event.payload.get("execution_stopped") is False,
            )
        return event

    async def record_message_deltas(
        self,
        session: Any,
        *,
        run_id: UUID,
        thread_id: UUID | None,
        events: Sequence[dict[str, Any]],
        trace_context: dict[str, Any] | None = None,
    ) -> list[RuntimeEventRow]:
        """Append one run's message deltas under a single cursor lock and flush."""
        if not events:
            return []
        run = await session.scalar(select(RunRow).where(RunRow.run_id == run_id).with_for_update())
        if run is None:
            raise RunOwnershipError(f"run {run_id} does not exist")
        if thread_id is not None and thread_id != run.thread_id:
            raise ValueError("event thread_id does not match the run thread")
        await self._assert_event_writer(session, run)
        thread_id = run.thread_id
        if thread_id is not None:
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
            sequence = max(thread.event_seq, run.event_seq, max_sequence)
        else:
            max_sequence = int(
                await session.scalar(
                    select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
                        RuntimeEventRow.run_id == run_id
                    )
                )
                or 0
            )
            sequence = max(run.event_seq, max_sequence)

        rows = []
        for event in events:
            sequence += 1
            namespace = list(event.get("namespace") or event.get("parent_ids") or [])
            payload = dict(event)
            payload.setdefault("timestamp", int(datetime.now(UTC).timestamp() * 1000))
            payload["trace"] = build_trace_metadata(
                event={"event": "messages", "namespace": namespace, **event},
                context={
                    "run_id": str(run_id),
                    "thread_id": str(thread_id) if thread_id is not None else None,
                    **(trace_context or {}),
                },
            )
            rows.append(
                RuntimeEventRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    sequence=sequence,
                    topic="messages",
                    namespace=namespace,
                    payload=payload,
                )
            )
        run.event_seq = sequence
        if thread_id is not None:
            thread.event_seq = sequence
        session.add_all(rows)
        await session.flush()
        return rows

    async def prune_expired_events(
        self,
        session: Any,
        *,
        retention_seconds: int,
        batch_size: int = 1000,
        now: datetime | None = None,
    ) -> int:
        """Delete one bounded batch from an old terminal run and retain replay watermarks."""
        if retention_seconds <= 0 or batch_size <= 0:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(seconds=retention_seconds)
        expired = exists(
            select(1).where(
                RuntimeEventRow.run_id == RunRow.run_id,
                RuntimeEventRow.created_at < cutoff,
                RuntimeEventRow.terminal.is_(False),
            )
        )
        run = await session.scalar(
            select(RunRow)
            .where(
                RunRow.status.in_([status.value for status in RunStatus if is_terminal(status)]),
                RunRow.updated_at < cutoff,
                expired,
            )
            .order_by(RunRow.updated_at, RunRow.run_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if run is None:
            return 0
        event_ids = list(
            (
                await session.scalars(
                    select(RuntimeEventRow.event_id)
                    .where(
                        RuntimeEventRow.run_id == run.run_id,
                        RuntimeEventRow.created_at < cutoff,
                        RuntimeEventRow.terminal.is_(False),
                    )
                    .order_by(RuntimeEventRow.sequence)
                    .limit(batch_size)
                )
            ).all()
        )
        if not event_ids:
            return 0
        deleted_sequences = list(
            (
                await session.scalars(
                    delete(RuntimeEventRow)
                    .where(RuntimeEventRow.event_id.in_(event_ids))
                    .returning(RuntimeEventRow.sequence)
                )
            ).all()
        )
        if not deleted_sequences:
            return 0
        watermark = max(deleted_sequences)
        run.event_pruned_through = max(run.event_pruned_through, watermark)
        if run.thread_id is not None:
            await session.execute(
                update(ThreadRow)
                .where(ThreadRow.thread_id == run.thread_id)
                .values(
                    event_pruned_through=func.greatest(ThreadRow.event_pruned_through, watermark)
                )
            )
        await session.flush()
        return len(deleted_sequences)

    async def requeue_expired(self, session: Any, *, now: datetime | None = None) -> int:
        now = now or await session.scalar(select(func.clock_timestamp()))
        self.last_requeued_events = []
        self.last_transition_events = []
        result = await session.execute(
            select(RunRow)
            .where(
                or_(
                    RunRow.status == RunStatus.RUNNING.value,
                    RunRow.reason == RunReason.CANCEL_REQUESTED.value,
                ),
                RunRow.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
        runs = list(result.scalars())
        count = 0
        for run in runs:
            cancelled = run.reason == RunReason.CANCEL_REQUESTED.value
            if cancelled:
                run.status = RunStatus.INTERRUPTED.value
                run.next_attempt_at = None
            elif not await self.can_retry(session, run):
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
                attempt = await self.attempt_count(session, run)
                run.next_attempt_at = now + timedelta(seconds=self.retry_delay(attempt))
            run.lease_owner = None
            run.lease_expires_at = None
            run.updated_at = now
            if run.thread_id is not None:
                thread = await session.scalar(
                    select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
                )
                if thread is not None:
                    thread.status = "idle"
                    if is_terminal(run.status):
                        thread.error = {"reason": run.reason}
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
                        **(
                            {
                                "execution_stopped": False,
                                "lease_fenced": True,
                                "generation": run.retry_count,
                            }
                            if is_terminal(run.status)
                            else {}
                        ),
                    },
                    namespace=[],
                    terminal=is_terminal(run.status),
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
