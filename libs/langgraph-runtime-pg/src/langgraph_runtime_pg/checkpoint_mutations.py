"""Transactional checkpoint maintenance for the pinned PostgreSQL layout."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import delete, or_, select, text

from langgraph_runtime_pg.models import RunCheckpointBaselineRow, RunRow, ThreadRow

# Inherited by LangGraph's asynchronous checkpoint tasks, never taken from input.
checkpoint_writer: ContextVar[tuple[str, str, int] | None] = ContextVar(
    "checkpoint_writer", default=None
)


class CheckpointConflict(ValueError):
    """Maintenance cannot safely modify this thread's current history."""


async def capture_baseline(session: Any, run: RunRow, thread: ThreadRow) -> None:
    """Caller owns the run and thread locks; retries keep the original baseline."""
    if await session.get(RunCheckpointBaselineRow, run.run_id) is not None:
        return
    params = {"thread_id": str(thread.thread_id)}
    checkpoints = list(
        (
            await session.execute(
                text("SELECT to_jsonb(c) FROM checkpoints c WHERE thread_id=:thread_id"), params
            )
        ).scalars()
    )
    writes = list(
        (
            await session.execute(
                text("SELECT to_jsonb(w) FROM checkpoint_writes w WHERE thread_id=:thread_id"),
                params,
            )
        ).scalars()
    )
    # ponytail: full baseline per run costs history-sized storage; replace with
    # a write journal only when measured retention costs justify that complexity.
    session.add(
        RunCheckpointBaselineRow(
            run_id=run.run_id,
            thread_id=thread.thread_id,
            checkpoints=checkpoints,
            writes=writes,
            projection={
                "_captured_at": (
                    await session.scalar(text("SELECT clock_timestamp()"))
                ).isoformat(),
                **{
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key in (
                        "values_",
                        "interrupts",
                        "status",
                        "error",
                        "graph_id",
                        "state_updated_at",
                    )
                    for value in (getattr(thread, key),)
                },
            },
        )
    )
    await session.flush()


async def rollback_run(session: Any, run: RunRow, *, validate_only: bool = False) -> None:
    """Restore a latest run's exact baseline and delete it in one transaction.

    Caller owns the run lock. Saver writes take that same lock before the
    thread lock, so neither a live nor an expired worker can write after commit.
    """
    if run.thread_id is None:
        if not validate_only:
            await session.delete(run)
            await session.flush()
        return
    thread = await session.scalar(
        select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
    )
    if thread is None:
        raise CheckpointConflict("thread no longer exists")
    active_other = await session.scalar(
        select(RunRow.run_id)
        .where(
            RunRow.thread_id == run.thread_id,
            RunRow.run_id != run.run_id,
            or_(RunRow.status == "running", RunRow.reason == "rollback"),
        )
        .limit(1)
    )
    if active_other:
        raise CheckpointConflict("another run is executing on this thread")
    baseline = await session.get(RunCheckpointBaselineRow, run.run_id)
    if baseline is None:
        if run.retry_count or run.status not in {"pending", "interrupted"}:
            raise CheckpointConflict("run has no safe rollback baseline")
    else:
        params = {"thread_id": str(run.thread_id)}
        current = list(
            (
                await session.execute(
                    text("SELECT to_jsonb(c) FROM checkpoints c WHERE thread_id=:thread_id"), params
                )
            ).scalars()
        )
        old = {(c["checkpoint_ns"], c["checkpoint_id"]): c for c in baseline.checkpoints}
        for checkpoint in current:
            key = (checkpoint["checkpoint_ns"], checkpoint["checkpoint_id"])
            if checkpoint == old.get(key):
                continue
            if str(checkpoint["metadata"].get("run_id", "")) != str(run.run_id):
                raise CheckpointConflict("later state depends on this run; rollback is unsafe")
        # A later run can write on an existing checkpoint without creating one.
        later = await session.scalar(
            select(RunCheckpointBaselineRow.run_id)
            .where(
                RunCheckpointBaselineRow.thread_id == run.thread_id,
                RunCheckpointBaselineRow.run_id != run.run_id,
                RunCheckpointBaselineRow.projection["_captured_at"].astext
                >= baseline.projection["_captured_at"],
            )
            .limit(1)
        )
        if later:
            raise CheckpointConflict("a later run has already started on this thread")
        if validate_only:
            return
        import json

        # Both tables and the resource rows use the same PostgreSQL transaction.
        # Blobs are immutable versioned values; retain them for all baselines.
        for table, rows in (
            ("checkpoint_writes", baseline.writes),
            ("checkpoints", baseline.checkpoints),
        ):
            await session.execute(text(f"DELETE FROM {table} WHERE thread_id=:thread_id"), params)
            if rows:
                await session.execute(
                    text(
                        f"INSERT INTO {table} SELECT * FROM jsonb_populate_recordset(NULL::{table}, CAST(:rows AS jsonb))"
                    ),
                    {"rows": json.dumps(rows)},
                )
        for field, value in baseline.projection.items():
            if field == "_captured_at":
                continue
            if field == "state_updated_at" and value:
                value = datetime.fromisoformat(value)
            setattr(thread, field, value)
    if not validate_only:
        await session.delete(run)
        await session.flush()


async def complete_rollbacks(*, owner: str | None = None, run_id: Any = None) -> None:
    """Resume durable cleanup after acknowledgement or an expired worker lease."""
    from langgraph_runtime_pg.database import connect

    async with connect() as conn:
        query = select(RunRow.run_id).where(RunRow.reason == "rollback").order_by(RunRow.run_id)
        if run_id is not None:
            query = query.where(RunRow.run_id == run_id)
        ids = (await conn.session.execute(query)).scalars().all()
    for pending_id in ids:
        try:
            async with connect() as conn:
                run = await conn.session.scalar(
                    select(RunRow)
                    .where(RunRow.run_id == pending_id, RunRow.reason == "rollback")
                    .with_for_update(skip_locked=True)
                )
                if run is None:
                    continue
                stopped = (
                    run.lease_owner is None
                    or (owner is not None and run.lease_owner == owner)
                    or (
                        run.lease_expires_at is not None
                        and run.lease_expires_at <= datetime.now(UTC)
                    )
                )
                if stopped:
                    await rollback_run(conn.session, run)
        except Exception:
            structlog.get_logger(__name__).exception(
                "rollback cleanup will retry", run_id=str(pending_id)
            )


async def prune_checkpoints(session: Any, thread: ThreadRow) -> None:
    """Keep each namespace head plus ancestors required to reconstruct deltas."""
    params = {"thread_id": str(thread.thread_id)}
    if await session.scalar(
        select(RunRow.run_id)
        .where(
            RunRow.thread_id == thread.thread_id,
            or_(RunRow.status == "running", RunRow.reason == "rollback"),
        )
        .limit(1)
    ):
        raise CheckpointConflict("cannot prune a thread while a run is executing")
    rows = list(
        (
            await session.execute(
                text(
                    "SELECT checkpoint_ns, checkpoint_id, parent_checkpoint_id, checkpoint, metadata FROM checkpoints "
                    "WHERE thread_id=:thread_id ORDER BY checkpoint_id DESC"
                ),
                params,
            )
        ).mappings()
    )
    blobs = set(
        (
            await session.execute(
                text(
                    "SELECT checkpoint_ns, channel, version FROM checkpoint_blobs "
                    "WHERE thread_id=:thread_id AND type <> 'empty'"
                ),
                params,
            )
        ).tuples()
    )
    by_key = {(r["checkpoint_ns"], r["checkpoint_id"]): r for r in rows}
    kept: set[tuple[str, str]] = set()
    namespaces: set[str] = set()
    for head in rows:
        ns = head["checkpoint_ns"]
        if ns in namespaces:
            continue
        namespaces.add(ns)
        unresolved = set(head["metadata"].get("counters_since_delta_snapshot", {}))
        row = head
        while row:
            key = (ns, row["checkpoint_id"])
            if key in kept:
                break
            kept.add(key)
            cp = row["checkpoint"]
            unresolved = {
                ch
                for ch in unresolved
                if ch not in cp.get("channel_values", {})
                and (ns, ch, str(cp.get("channel_versions", {}).get(ch))) not in blobs
            }
            if not unresolved:
                break
            row = by_key.get((ns, row["parent_checkpoint_id"]))
    for ns, checkpoint_id in by_key.keys() - kept:
        key_params = {**params, "ns": ns, "checkpoint_id": checkpoint_id}
        for table in ("checkpoint_writes", "checkpoints"):
            await session.execute(
                text(
                    f"DELETE FROM {table} WHERE thread_id=:thread_id AND checkpoint_ns=:ns AND checkpoint_id=:checkpoint_id"
                ),
                key_params,
            )
    # User-directed maintenance supersedes old rollback baselines.
    await session.execute(
        delete(RunCheckpointBaselineRow).where(
            RunCheckpointBaselineRow.thread_id == thread.thread_id
        )
    )
