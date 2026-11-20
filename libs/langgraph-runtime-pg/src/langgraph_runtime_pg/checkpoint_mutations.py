"""Transactional checkpoint maintenance for the pinned PostgreSQL layout."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import jsonpatch
import structlog
from sqlalchemy import delete, or_, select, text

from langgraph_runtime_pg.models import (
    RunCheckpointBaselineRow,
    RunLeaseRow,
    RunRow,
    RuntimeEventRow,
    ThreadRow,
)

# Inherited by LangGraph's asynchronous checkpoint tasks, never taken from input.
checkpoint_writer: ContextVar[tuple[str, str, int] | None] = ContextVar(
    "checkpoint_writer", default=None
)


class CheckpointConflict(ValueError):
    """Maintenance cannot safely modify this thread's current history."""


async def capture_baseline(session: Any, run: RunRow, thread: ThreadRow | None) -> None:
    """Caller owns the run and thread locks; retries keep the original baseline."""
    if await session.get(RunCheckpointBaselineRow, run.run_id) is not None:
        return
    head = await session.scalar(
        text("""
        SELECT checkpoint_id FROM checkpoints WHERE thread_id=:thread_id
            AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1
    """),
        {"thread_id": str(thread.thread_id if thread else run.run_id)},
    )
    session.add(
        RunCheckpointBaselineRow(
            run_id=run.run_id,
            thread_id=thread.thread_id if thread else None,
            heads={"": head},
            projection={
                "_captured_at": (
                    await session.scalar(text("SELECT clock_timestamp()"))
                ).isoformat(),
                **{
                    key: value.isoformat() if isinstance(value, datetime) else value
                    for key in (
                        "interrupts",
                        "status",
                        "error",
                        "graph_id",
                        "state_updated_at",
                    )
                    if thread is not None
                    for value in (getattr(thread, key),)
                },
                "values_undo": [],
            },
        )
    )
    await session.flush()


async def project_run_values(session: Any, run_id: Any, thread: ThreadRow, values: Any) -> None:
    """Persist only the inverse JSON changes, atomically with the run's result."""
    boundary = await session.get(RunCheckpointBaselineRow, run_id)
    if boundary is None:
        raise CheckpointConflict("run has no checkpoint boundary for its result")
    boundary.projection = {
        **boundary.projection,
        "values_undo": jsonpatch.make_patch(values, thread.values_).patch
        + boundary.projection["values_undo"],
    }
    thread.values_ = values


async def rollback_receipt(session: Any, run_id: Any) -> RuntimeEventRow | None:
    return await session.scalar(
        select(RuntimeEventRow).where(
            RuntimeEventRow.topic == "cancel.receipt",
            RuntimeEventRow.payload["run_id"].astext == str(run_id),
        )
    )


async def _delete_rolled_back_run(session: Any, run: RunRow, execution_stopped: bool) -> None:
    from langgraph_runtime_pg.run_store import RunRepository

    repository = RunRepository()
    worker_owner = run.lease_owner
    run.status, run.reason = "interrupted", "rollback"
    run.lease_owner = None
    terminal = await repository.record_event(
        session,
        run_id=run.run_id,
        thread_id=run.thread_id,
        topic="lifecycle",
        payload={
            "event": "lifecycle",
            "status": "interrupted",
            "reason": "rollback",
            "execution_stopped": execution_stopped,
        },
        terminal=True,
    )
    from langgraph_runtime_pg.webhooks import snapshot_deliveries

    await snapshot_deliveries(session, run, terminal, awaiting_stop=not execution_stopped)
    terminal.run_id = None
    receipt = await repository.record_event(
        session,
        run_id=run.run_id,
        thread_id=run.thread_id,
        topic="cancel.receipt",
        payload={
            "run_id": str(run.run_id),
            "action": "rollback",
            "rollback_completed": True,
            "execution_stopped": execution_stopped,
            "generation": run.retry_count,
            "worker_owner": worker_owner,
            "terminal_event_id": str(terminal.event_id),
        },
    )
    # Keep acknowledgement after the Run and its checkpoint baseline are deleted.
    receipt.run_id = None
    await session.flush()
    await session.delete(run)
    await session.flush()


async def rollback_run(
    session: Any, run: RunRow, *, validate_only: bool = False, execution_stopped: bool = True
) -> None:
    """Restore a latest run's exact baseline and delete it in one transaction.

    Caller owns the run lock. Saver writes take that same lock before the
    thread lock, so neither a live nor an expired worker can write after commit.
    """
    thread = (
        await session.scalar(
            select(ThreadRow).where(ThreadRow.thread_id == run.thread_id).with_for_update()
        )
        if run.thread_id is not None
        else None
    )
    if run.thread_id is not None and thread is None:
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
    if run.thread_id is not None and active_other:
        raise CheckpointConflict("another run is executing on this thread")
    baseline = await session.get(RunCheckpointBaselineRow, run.run_id)
    if baseline is None:
        if run.retry_count or run.status not in {"pending", "interrupted"}:
            raise CheckpointConflict("run has no safe rollback baseline")
    else:
        # A later run can write on an existing checkpoint without creating one.
        later = await session.scalar(
            select(RunCheckpointBaselineRow.run_id)
            .where(
                RunCheckpointBaselineRow.thread_id == run.thread_id,
                RunCheckpointBaselineRow.run_id != run.run_id,
                RunCheckpointBaselineRow.sequence > baseline.sequence,
            )
            .limit(1)
        )
        if run.thread_id is not None and later:
            raise CheckpointConflict("a later run has already started on this thread")
        if validate_only:
            return
        from langgraph_runtime_pg.checkpoint_journal import key_match

        params = {"run_id": run.run_id}
        await session.execute(
            text(
                "SELECT set_config('graphharbor.checkpoint_format', 'journal-v1', true), "
                "set_config('graphharbor.checkpoint_writer', '', true)"
            )
        )
        for table in ("checkpoint_writes", "checkpoints", "checkpoint_blobs"):
            await session.execute(
                text(f"""
                DELETE FROM {table} t USING run_checkpoint_undo u
                WHERE u.run_id=:run_id AND u.table_name='{table}' AND {key_match(table)}
            """),
                params,
            )
            await session.execute(
                text(f"""INSERT INTO {table} SELECT restored.*
                FROM run_checkpoint_undo u,
                    jsonb_populate_record(NULL::{table}, u.before_image) restored
                WHERE u.run_id=:run_id AND u.table_name='{table}' AND u.before_image IS NOT NULL
            """),
                params,
            )
        for field, value in baseline.projection.items():
            if thread is None or field == "_captured_at":
                continue
            if field == "values_undo":
                thread.values_ = jsonpatch.apply_patch(thread.values_, value)
                continue
            if field == "state_updated_at" and value:
                value = datetime.fromisoformat(value)
            setattr(thread, field, value)
    if not validate_only:
        await _delete_rolled_back_run(session, run, execution_stopped)


async def complete_rollbacks(
    *, owner: str | None = None, run_id: Any = None, generation: int | None = None
) -> None:
    """Resume durable cleanup after acknowledgement or an expired worker lease."""
    from langgraph_runtime_pg.database import connect

    if owner is not None and generation is not None:
        from langgraph_runtime_pg.run_store import RunRepository

        async with connect() as conn:
            run = await conn.session.scalar(
                select(RunRow)
                .where(
                    RunRow.run_id == run_id,
                    RunRow.reason == "rollback",
                    RunRow.lease_owner == owner,
                    RunRow.retry_count == generation,
                )
                .with_for_update()
            )
            lease = await conn.session.get(RunLeaseRow, run_id)
            if run is None:
                receipt = await rollback_receipt(conn.session, run_id)
                if (
                    receipt is not None
                    and receipt.payload.get("generation") == generation
                    and receipt.payload.get("worker_owner") == owner
                ):
                    from langgraph_runtime_pg.models import RunCallbackDeliveryRow

                    receipt.payload = {**receipt.payload, "execution_stopped": True}
                    terminal = await conn.session.get(
                        RuntimeEventRow, UUID(receipt.payload["terminal_event_id"])
                    )
                    if terminal is not None:
                        terminal.payload = {**terminal.payload, "execution_stopped": True}
                    deliveries = (
                        await conn.session.scalars(
                            select(RunCallbackDeliveryRow)
                            .where(
                                RunCallbackDeliveryRow.run_id == run_id,
                                RunCallbackDeliveryRow.state == "awaiting_stop",
                            )
                            .with_for_update()
                        )
                    ).all()
                    for delivery in deliveries:
                        import json

                        body = json.loads(delivery.body)
                        if delivery.profile == "terminal":
                            body["execution_stopped"] = True
                            body["occurred_at"] = datetime.now(UTC).isoformat()
                        delivery.body = json.dumps(body, sort_keys=True, separators=(",", ":"))
                        delivery.state = "pending"
                        delivery.next_attempt_at = datetime.now(UTC)
            if (
                run is not None
                and lease is not None
                and (
                    lease.owner == owner
                    and lease.generation == generation
                    and lease.expires_at > datetime.now(UTC)
                )
            ):
                await RunRepository().record_event(
                    conn.session,
                    run_id=run.run_id,
                    thread_id=run.thread_id,
                    topic="cancel.stopped",
                    payload={"execution_stopped": True, "generation": generation},
                )
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
                lease = await conn.session.get(RunLeaseRow, run.run_id)
                acknowledged = await conn.session.scalar(
                    select(RuntimeEventRow.event_id)
                    .where(
                        RuntimeEventRow.run_id == run.run_id,
                        RuntimeEventRow.topic == "cancel.stopped",
                        RuntimeEventRow.payload["generation"].as_integer() == run.retry_count,
                    )
                    .limit(1)
                )
                expired = (
                    run.lease_owner is None
                    or lease is None
                    or lease.expires_at <= datetime.now(UTC)
                    or (
                        run.lease_expires_at is not None
                        and run.lease_expires_at <= datetime.now(UTC)
                    )
                )
                if acknowledged or expired:
                    await rollback_run(
                        conn.session,
                        run,
                        execution_stopped=bool(acknowledged) or run.retry_count == 0,
                    )
        except Exception:
            structlog.get_logger(__name__).exception(
                "rollback cleanup will retry", run_id=str(pending_id)
            )


async def prune_checkpoints(session: Any, thread: ThreadRow) -> None:
    """Keep each namespace head plus ancestors required to reconstruct deltas."""
    if thread.deletion_requested_at is not None:
        raise CheckpointConflict("thread deletion has been requested")
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
    # The caller holds the thread lock through commit, including every deletion batch.
    await session.execute(
        text("""
        CREATE TEMP TABLE IF NOT EXISTS graphharbor_prune_keep (
            checkpoint_ns text, checkpoint_id text, parent_checkpoint_id text,
            unresolved text[], PRIMARY KEY (checkpoint_ns, checkpoint_id)
        ) ON COMMIT DROP
    """)
    )
    await session.execute(text("TRUNCATE graphharbor_prune_keep"))
    await session.execute(
        text("""
        WITH RECURSIVE delta_channels AS (
            SELECT DISTINCT checkpoint_ns, jsonb_object_keys(COALESCE(
                metadata->'counters_since_delta_snapshot', '{}'::jsonb)) AS channel
            FROM checkpoints WHERE thread_id=:thread_id
        ), heads AS (
            SELECT DISTINCT ON (checkpoint_ns) * FROM checkpoints
            WHERE thread_id=:thread_id ORDER BY checkpoint_ns, checkpoint_id DESC
        ), chain AS (
            SELECT c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id,
                ARRAY(SELECT channel FROM delta_channels d
                WHERE d.checkpoint_ns=c.checkpoint_ns
                AND NOT COALESCE(c.checkpoint->'channel_values', '{}'::jsonb) ? d.channel
                AND NOT EXISTS (SELECT 1 FROM checkpoint_blobs b
                    WHERE b.thread_id=:thread_id AND b.checkpoint_ns=c.checkpoint_ns
                    AND b.channel=d.channel AND b.version=c.checkpoint->'channel_versions'->>d.channel
                    AND b.type <> 'empty')) AS unresolved
            FROM heads c
            UNION
            SELECT c.checkpoint_ns, c.checkpoint_id, c.parent_checkpoint_id,
                ARRAY(SELECT ch FROM unnest(p.unresolved) ch
                    WHERE NOT COALESCE(c.checkpoint->'channel_values', '{}'::jsonb) ? ch
                    AND NOT EXISTS (SELECT 1 FROM checkpoint_blobs b
                        WHERE b.thread_id=:thread_id AND b.checkpoint_ns=c.checkpoint_ns
                        AND b.channel=ch AND b.version=c.checkpoint->'channel_versions'->>ch
                        AND b.type <> 'empty'))
            FROM chain p JOIN checkpoints c ON c.thread_id=:thread_id
                AND c.checkpoint_ns=p.checkpoint_ns
                AND c.checkpoint_id=p.parent_checkpoint_id
            WHERE cardinality(p.unresolved) > 0
        ) INSERT INTO graphharbor_prune_keep SELECT * FROM chain
    """),
        params,
    )
    missing = await session.scalar(
        text("""
        SELECT EXISTS(SELECT 1 FROM graphharbor_prune_keep p
            WHERE cardinality(p.unresolved)>0 AND p.parent_checkpoint_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM graphharbor_prune_keep a
                WHERE a.checkpoint_ns=p.checkpoint_ns
                AND a.checkpoint_id=p.parent_checkpoint_id))
    """)
    )
    if missing:
        raise CheckpointConflict("delta history has a missing ancestor; prune refused")
    # Explicit maintenance closes the old rollback window before releasing its references.
    await session.execute(
        delete(RunCheckpointBaselineRow).where(
            RunCheckpointBaselineRow.thread_id == thread.thread_id
        )
    )
    for table in ("checkpoint_writes", "checkpoints"):
        await _delete_batches(
            session,
            table,
            """
            thread_id=:thread_id AND NOT EXISTS (
                SELECT 1 FROM graphharbor_prune_keep k
                WHERE k.checkpoint_ns=t.checkpoint_ns AND k.checkpoint_id=t.checkpoint_id
            )
        """,
            params,
        )
    await _delete_batches(
        session,
        "checkpoint_blobs",
        """
        thread_id=:thread_id AND NOT EXISTS (
            SELECT 1 FROM checkpoints c,
                jsonb_each_text(c.checkpoint->'channel_versions') v
            WHERE c.thread_id=t.thread_id AND c.checkpoint_ns=t.checkpoint_ns
                AND v.key=t.channel AND v.value=t.version
        )
    """,
        params,
    )


async def _delete_batches(session: Any, table: str, predicate: str, params: dict) -> None:
    """Bound statement size without releasing the caller's maintenance lock."""
    while await session.scalar(
        text(f"""
        WITH victims AS (SELECT t.ctid FROM {table} t WHERE {predicate} LIMIT 500),
        removed AS (DELETE FROM {table} t USING victims v WHERE t.ctid=v.ctid RETURNING 1)
        SELECT count(*) FROM removed
    """),
        params,
    ):
        pass
