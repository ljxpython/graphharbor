"""Generic thread retention; application policy lives in the caller and hooks."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import structlog
from sqlalchemy import and_, delete, exists, or_, select, text

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import (
    CronRow,
    RetryCounterRow,
    RunCancellationRow,
    RunLeaseRow,
    RunRow,
    RuntimeEventRow,
    ThreadDeletionRow,
    ThreadRow,
)
from langgraph_runtime_pg.protocol import RunStatus
from langgraph_runtime_pg.run_state import is_terminal
from langgraph_runtime_pg.run_store import stopped_event

Hook = Callable[[dict[str, Any]], Awaitable[bool | None]]
_HOOKS: dict[str, Hook] = {}
_SPECS: dict[str, Any] = {}
logger = structlog.get_logger(__name__)


class RetentionConflict(ValueError):
    pass


def configure_retention(config: dict[str, Any], loader: Callable[[str], Any]) -> None:
    global _HOOKS, _SPECS
    specs = config.get("thread_lifecycle") or {}
    if not isinstance(specs, dict) or set(specs) - {
        "before_delete",
        "after_delete",
        "tombstone_metadata_keys",
    }:
        raise ValueError("unsupported thread_lifecycle setting")
    keys = specs.get("tombstone_metadata_keys")
    if "tombstone_metadata_keys" in specs and (
        not isinstance(keys, list)
        or any(not isinstance(key, str) or not key or key.startswith("$") for key in keys)
        or len(set(keys)) != len(keys)
    ):
        raise ValueError("tombstone_metadata_keys must be distinct nonempty metadata keys")
    paths = {name: spec for name, spec in specs.items() if name != "tombstone_metadata_keys"}
    if any(not isinstance(value, str) or not value for value in paths.values()):
        raise ValueError("thread lifecycle hooks must be import paths")
    hooks = {name: loader(spec) for name, spec in paths.items()}
    if any(not callable(hook) for hook in hooks.values()):
        raise ValueError("thread lifecycle hooks must be callable")
    _HOOKS, _SPECS = hooks, dict(specs)


def validate_policy(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"mode", "ttl_minutes"}:
        raise ValueError("retention requires mode and ttl_minutes")
    minutes = value["ttl_minutes"]
    if value["mode"] not in {"fixed", "idle"}:
        raise ValueError("retention.mode must be fixed or idle")
    if (
        isinstance(minutes, bool)
        or not isinstance(minutes, (int, float))
        or not math.isfinite(minutes)
        or not 0 < minutes <= 52560000
    ):
        raise ValueError("retention.ttl_minutes must be finite, positive and at most 52560000")
    return dict(value)


def official_ttl(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("strategy", "delete") != "delete":
        raise ValueError("automatic ttl supports delete only; use threads/prune for keep_latest")
    if set(value) - {"strategy", "ttl"} or "ttl" not in value:
        raise ValueError("ttl requires ttl in minutes")
    return validate_policy({"mode": "fixed", "ttl_minutes": value["ttl"]})


def set_policy(thread: ThreadRow, policy: dict[str, Any] | None) -> None:
    if thread.deletion_requested_at is not None:
        raise RetentionConflict("thread deletion has been requested")
    now = datetime.now(UTC)
    thread.retention = policy
    # Installing a policy does not invent historical user activity.
    thread.last_activity_at = thread.last_activity_at or now
    thread.expires_at = (
        (thread.last_activity_at if policy["mode"] == "idle" else now)
        + timedelta(minutes=policy["ttl_minutes"])
        if policy
        else None
    )


def touch(thread: ThreadRow) -> None:
    if thread.deletion_requested_at is not None:
        raise RetentionConflict("thread deletion has been requested")
    thread.last_activity_at = datetime.now(UTC)
    if thread.retention and thread.retention["mode"] == "idle":
        thread.expires_at = thread.last_activity_at + timedelta(
            minutes=thread.retention["ttl_minutes"]
        )


def retention_status(thread: ThreadRow) -> dict[str, Any]:
    return {
        "policy": thread.retention,
        **{
            name: value.isoformat() if value else None
            for name in ("last_activity_at", "expires_at", "deletion_requested_at")
            for value in (getattr(thread, name),)
        },
    }


def deletion_status(job: ThreadDeletionRow) -> dict[str, Any]:
    return {
        "task_id": str(job.task_id),
        "thread_id": str(job.thread_id),
        "reason": job.reason,
        "state": job.state,
        "attempts": job.attempts,
        "requested_at": job.requested_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "last_error": job.last_error,
    }


async def request_deletion(
    session: Any, thread: ThreadRow, *, reason: str = "manual"
) -> ThreadDeletionRow:
    """Caller owns the thread lock shared with submissions and touches."""
    job = await session.get(ThreadDeletionRow, thread.thread_id)
    if job is None:
        thread.deletion_requested_at = datetime.now(UTC)
        job = ThreadDeletionRow(
            thread_id=thread.thread_id,
            metadata_=dict(thread.metadata_),
            reason=reason,
            hooks=dict(_SPECS),
            state="before_delete",
        )
        session.add(job)
        await session.flush()
    return job


async def _blocked(session: Any, thread_id: UUID, *, reason: str) -> bool:
    runs = (await session.scalars(select(RunRow).where(RunRow.thread_id == thread_id))).all()
    for run in runs:
        if await stopped_event(session, run) is None:
            return True
    if await session.scalar(
        select(RunLeaseRow.run_id)
        .join(RunRow)
        .where(RunRow.thread_id == thread_id, RunLeaseRow.expires_at > datetime.now(UTC))
        .limit(1)
    ):
        return True
    if await session.scalar(
        select(RuntimeEventRow.event_id)
        .where(
            RuntimeEventRow.thread_id == thread_id,
            RuntimeEventRow.topic == "cancel.receipt",
            RuntimeEventRow.payload["execution_stopped"].as_boolean().is_(False),
        )
        .limit(1)
    ):
        return True
    return bool(
        reason == "ttl"
        and await session.scalar(
            select(CronRow.cron_id)
            .where(CronRow.thread_id == thread_id, CronRow.enabled.is_(True))
            .limit(1)
        )
    )


def _eligible():
    stopped = exists(
        select(1).where(
            RuntimeEventRow.run_id == RunRow.run_id,
            RuntimeEventRow.terminal.is_(True),
            RuntimeEventRow.payload["execution_stopped"].as_boolean().is_distinct_from(False),
            or_(
                RunRow.reason.is_(None),
                RunRow.reason.notin_(["cancel_requested", "rollback"]),
                RuntimeEventRow.payload["execution_stopped"].as_boolean().is_(True),
            ),
        )
    )
    # Exclude protected rows before LIMIT so an old active schedule cannot starve others.
    return and_(
        ~exists(
            select(1).where(CronRow.thread_id == ThreadRow.thread_id, CronRow.enabled.is_(True))
        ),
        ~exists(
            select(1).where(
                RunRow.thread_id == ThreadRow.thread_id,
                or_(
                    RunRow.status.notin_([s.value for s in RunStatus if is_terminal(s)]),
                    RunRow.lease_owner.is_not(None),
                    ~stopped,
                ),
            )
        ),
        ~exists(
            select(1)
            .select_from(RunLeaseRow)
            .join(RunRow)
            .where(
                RunRow.thread_id == ThreadRow.thread_id,
                RunLeaseRow.expires_at > datetime.now(UTC),
            )
        ),
        ~exists(
            select(1).where(
                RuntimeEventRow.thread_id == ThreadRow.thread_id,
                RuntimeEventRow.topic == "cancel.receipt",
                RuntimeEventRow.payload["execution_stopped"].as_boolean().is_(False),
            )
        ),
    )


async def _engine_delete(job_id: UUID, owner: str) -> None:
    async with connect() as conn:
        session = conn.session
        # Fail and retry on lock contention instead of inverting worker/cron locks.
        await session.execute(text("SET LOCAL lock_timeout = '500ms'"))
        await session.execute(text("SET LOCAL statement_timeout = '10s'"))
        job = await session.scalar(
            select(ThreadDeletionRow)
            .where(ThreadDeletionRow.thread_id == job_id, ThreadDeletionRow.lease_owner == owner)
            .with_for_update()
        )
        if job is None:
            raise RetentionConflict("deletion claim expired")
        thread = await session.scalar(
            select(ThreadRow).where(ThreadRow.thread_id == job_id).with_for_update()
        )
        if thread is not None:
            if await _blocked(session, job_id, reason=job.reason):
                raise RetentionConflict("thread execution or schedule has not stopped")
            # Same database/transaction as resources: no half-deleted checkpoint history.
            for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE thread_id=:id"), {"id": str(job_id)}
                )
            run_ids = select(RunRow.run_id).where(RunRow.thread_id == job_id)
            await session.execute(
                delete(RetryCounterRow).where(RetryCounterRow.run_id.in_(run_ids))
            )
            await session.execute(
                delete(RunCancellationRow).where(RunCancellationRow.thread_id == job_id)
            )
            await session.execute(delete(CronRow).where(CronRow.thread_id == job_id))
            await session.execute(delete(RunRow).where(RunRow.thread_id == job_id))
            await session.delete(thread)
        job.state = "after_delete"


async def advance_deletion(thread_id: UUID) -> bool:
    """At-least-once hooks use task_id for idempotence; never hold SQL locks during I/O."""
    owner = uuid4().hex
    async with connect() as conn:
        job = await conn.session.scalar(
            select(ThreadDeletionRow)
            .where(
                ThreadDeletionRow.thread_id == thread_id,
                ThreadDeletionRow.state != "completed",
                ThreadDeletionRow.next_attempt_at <= datetime.now(UTC),
                or_(
                    ThreadDeletionRow.lease_expires_at.is_(None),
                    ThreadDeletionRow.lease_expires_at < datetime.now(UTC),
                ),
            )
            .with_for_update(skip_locked=True)
        )
        if job is None:
            existing = await conn.session.get(ThreadDeletionRow, thread_id)
            return existing is not None and existing.state == "completed"
        job.lease_owner = owner
        job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=2)
        job.attempts += 1
        event = {**deletion_status(job), "metadata": dict(job.metadata_)}
        state, required = job.state, dict(job.hooks)
    try:
        # An incomplete configuration must never bypass an already-promised handoff.
        if any(_SPECS.get(name) != spec for name, spec in required.items()):
            raise RetentionConflict("required lifecycle hook configuration is unavailable")
        if state == "before_delete":
            async with connect() as conn:
                if await _blocked(conn.session, thread_id, reason=event["reason"]):
                    raise RetentionConflict("thread execution or schedule has not stopped")
            if "before_delete" in required:
                async with asyncio.timeout(30):
                    if await _HOOKS["before_delete"](event) is False:
                        raise RetentionConflict("application deletion precondition pending")
            await _engine_delete(thread_id, owner)
            event["state"] = "after_delete"
        if "after_delete" in required:
            async with asyncio.timeout(30):
                if await _HOOKS["after_delete"](event) is False:
                    raise RetentionConflict("application deletion cleanup pending")
        async with connect() as conn:
            job = await conn.session.scalar(
                select(ThreadDeletionRow)
                .where(
                    ThreadDeletionRow.thread_id == thread_id, ThreadDeletionRow.lease_owner == owner
                )
                .with_for_update()
            )
            if job is None:
                return False
            # Keep recovery metadata until every handoff succeeds, then keep only auth facts.
            keys = required.get("tombstone_metadata_keys", _SPECS.get("tombstone_metadata_keys"))
            if keys is not None:
                job.metadata_ = {key: job.metadata_[key] for key in keys if key in job.metadata_}
            job.state, job.completed_at = "completed", datetime.now(UTC)
            job.lease_owner = job.lease_expires_at = job.last_error = None
        return True
    except Exception as exc:
        async with connect() as conn:
            job = await conn.session.scalar(
                select(ThreadDeletionRow)
                .where(
                    ThreadDeletionRow.thread_id == thread_id, ThreadDeletionRow.lease_owner == owner
                )
                .with_for_update()
            )
            if job is not None:
                # Do not persist exception text: application hooks can include secrets.
                job.last_error = (
                    str(exc) if isinstance(exc, RetentionConflict) else type(exc).__name__
                )
                job.next_attempt_at = datetime.now(UTC) + timedelta(
                    seconds=min(3600, 2 ** min(job.attempts, 11))
                )
                job.lease_owner = job.lease_expires_at = None
        logger.warning(
            "thread deletion pending", thread_id=str(thread_id), error=type(exc).__name__
        )
        return False


async def maintain_retention(*, apply: bool = False, limit: int = 100) -> dict[str, Any]:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    now = datetime.now(UTC)
    async with connect() as conn:
        query = (
            select(ThreadRow)
            .where(
                ThreadRow.expires_at <= now, ThreadRow.deletion_requested_at.is_(None), _eligible()
            )
            .order_by(ThreadRow.expires_at)
            .limit(limit)
        )
        rows = (
            await conn.session.scalars(query.with_for_update(skip_locked=True) if apply else query)
        ).all()
        due = [str(row.thread_id) for row in rows]
        if apply:
            for row in rows:
                if not await _blocked(conn.session, row.thread_id, reason="ttl"):
                    await request_deletion(conn.session, row, reason="ttl")
        ids = (
            await conn.session.scalars(
                select(ThreadDeletionRow.thread_id)
                .where(
                    ThreadDeletionRow.state != "completed",
                    ThreadDeletionRow.next_attempt_at <= datetime.now(UTC),
                )
                .order_by(ThreadDeletionRow.next_attempt_at)
                .limit(limit)
            )
        ).all()
    completed = 0
    if apply:
        for thread_id in ids:
            completed += int(await advance_deletion(thread_id))
    return {
        "apply": apply,
        "due_threads": due,
        "pending_tasks": [str(i) for i in ids],
        "completed": completed,
    }


async def retention_loop() -> None:
    while True:
        try:
            result = await maintain_retention(apply=True)
            if result["due_threads"] or result["pending_tasks"]:
                logger.info("thread retention maintenance", **result)
        except Exception:
            logger.exception("thread retention maintenance failed")
        await asyncio.sleep(60)
