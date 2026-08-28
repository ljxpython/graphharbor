"""Small production worker loop built on public LangGraph APIs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from langgraph_runtime_pg.checkpoint import get_checkpointer, reconnect_checkpointer
from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.graph_executor import invoke_graph, resume_command, thread_config
from langgraph_runtime_pg.graph_registry import GraphRegistry
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
from langgraph_runtime_pg.production import configure_structured_logging
from langgraph_runtime_pg.protocol import RunReason, RunStatus, protocol_event
from langgraph_runtime_pg.redis_stream import (
    Message,
    bg_job_heartbeat_secs,
    clear_run_heartbeat,
    dequeue_run_hint,
    get_stream_manager,
    set_run_heartbeat,
    wait_for_queue_wake,
    wake_run_queue,
)
from langgraph_runtime_pg.run_state import is_terminal
from langgraph_runtime_pg.run_store import RunRepository

logger = structlog.stdlib.get_logger(__name__)


class RunCancelled(Exception):
    pass


def _is_infrastructure_error(exc: BaseException) -> bool:
    return isinstance(
        exc, (TimeoutError, ConnectionError, OSError, DBAPIError)
    ) or exc.__class__.__module__.startswith("psycopg")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _interrupts_payload(interrupts: Any) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in interrupts or ():
        values.append(
            {
                "id": str(getattr(item, "id", "")),
                "value": _jsonable(getattr(item, "value", item)),
                "ns": _jsonable(getattr(item, "ns", None)),
            }
        )
    return values


class ProductionWorker:
    def __init__(self, registry: GraphRegistry, *, owner: str | None = None) -> None:
        self.registry = registry
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}"
        self.repository = RunRepository()
        self.stop_event = asyncio.Event()

    async def _publish_event(
        self, run_id: UUID, thread_id: UUID | None, event: dict[str, Any]
    ) -> None:
        topic = str(event.get("event", "custom"))
        async with connect() as conn:
            durable = await self.repository.record_event(
                conn.session,
                run_id=run_id,
                thread_id=thread_id,
                topic=topic,
                payload=event,
                namespace=list(event.get("namespace") or event.get("parent_ids") or []),
            )
        await self._fanout_durable_event(durable)

    async def _fanout_durable_event(self, durable: Any) -> None:
        try:
            manager = get_stream_manager()
            run_id = durable.run_id
            thread_id = durable.thread_id
            event = durable.payload
            if run_id is None:
                return
            await manager.put(
                run_id,
                thread_id,
                Message(
                    topic=f"event:{durable.topic}".encode(),
                    data=json.dumps(
                        {
                            "id": str(durable.event_id),
                            "seq": durable.sequence,
                            "run_id": str(run_id),
                            "thread_id": str(thread_id) if thread_id else None,
                            "event": event,
                        },
                        separators=(",", ":"),
                        default=str,
                    ).encode(),
                ),
                resumable=True,
            )
            if thread_id is not None:
                wire_event = protocol_event(
                    event_id=str(durable.event_id),
                    sequence=durable.sequence,
                    run_id=str(run_id),
                    thread_id=str(thread_id),
                    event=event,
                )
                await manager.put_thread(
                    thread_id,
                    Message(
                        topic=f"protocol:{wire_event['method']}".encode(),
                        data=json.dumps(wire_event, separators=(",", ":"), default=str).encode(),
                    ),
                )
        except Exception:
            logger.warning(
                "event transport unavailable",
                run_id=str(getattr(durable, "run_id", "")),
                topic=str(getattr(durable, "topic", "lifecycle")),
            )

    async def _cancel_requested(self, run_id: UUID, thread_id: UUID | None) -> bool:
        try:
            if await get_stream_manager().aget_control_key(run_id, thread_id) is not None:
                return True
        except Exception:
            logger.debug("control transport unavailable", run_id=str(run_id), exc_info=True)
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
        return row is None or is_terminal(row.status)

    async def _heartbeat(
        self, run_id: UUID, thread_id: UUID | None, cancel_event: asyncio.Event
    ) -> None:
        interval = max(bg_job_heartbeat_secs() / 2.0, 1.0)
        while True:
            if self.stop_event.is_set():
                cancel_event.set()
                return
            await asyncio.sleep(interval)
            try:
                if self.stop_event.is_set():
                    cancel_event.set()
                    return
                async with connect() as conn:
                    if not await self.repository.renew(conn.session, run_id, self.owner):
                        cancel_event.set()
                        return
                await set_run_heartbeat(run_id)
                if await self._cancel_requested(run_id, thread_id):
                    cancel_event.set()
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("run heartbeat failed", run_id=str(run_id))
                cancel_event.set()
                return

    async def run_once(self) -> bool:
        if self.stop_event.is_set():
            return False
        with contextlib.suppress(Exception):
            await dequeue_run_hint()
        async with connect() as conn:
            run = await self.repository.claim_next(conn.session, self.owner)
            if run is None:
                return False
            metric_inc("graphharbor_runs_claimed_total")
            run_id = run.run_id
            thread_id = run.thread_id
            thread = await conn.session.get(ThreadRow, thread_id) if thread_id else None
            assistant = await conn.session.scalar(
                select(AssistantRow).where(AssistantRow.assistant_id == run.assistant_id)
            )
            if assistant is None:
                await self.repository.fail(
                    conn.session,
                    run_id,
                    self.owner,
                    infrastructure=False,
                    reason=RunReason.BUSINESS_ERROR,
                )
                return True
            graph_id = assistant.graph_id
            command = resume_command(run.kwargs.get("command"))
            input_value = command or run.kwargs.get("input", run.kwargs)
            runtime_context = run.kwargs.get("context")
            if not isinstance(runtime_context, dict):
                runtime_context = None
            if thread is not None:
                thread.status = "busy"
                await conn.session.flush()

        cancel_event = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(run_id, thread_id, cancel_event), name=f"heartbeat-{run_id}"
        )
        await set_run_heartbeat(run_id)
        try:
            await self._publish_event(
                run_id,
                thread_id,
                {"event": "lifecycle", "status": RunStatus.RUNNING.value},
            )
            if await self._cancel_requested(run_id, thread_id):
                raise RunCancelled

            async def on_event(event: dict[str, Any]) -> None:
                if self.stop_event.is_set():
                    raise RunCancelled
                if await self._cancel_requested(run_id, thread_id):
                    raise RunCancelled
                await self._publish_event(run_id, thread_id, event)

            execution = asyncio.create_task(
                invoke_graph(
                    self.registry.get(graph_id),
                    input_value,
                    config=thread_config(
                        str(thread_id) if thread_id else None,
                        assistant_id=str(run.assistant_id),
                        graph_id=str(graph_id),
                        runtime_context=runtime_context,
                    ),
                    on_event=on_event,
                ),
                name=f"run-{run_id}",
            )
            cancellation = asyncio.create_task(cancel_event.wait(), name=f"cancel-{run_id}")
            done, _ = await asyncio.wait(
                {execution, cancellation}, return_when=asyncio.FIRST_COMPLETED
            )
            if cancellation in done:
                execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
                raise RunCancelled
            cancellation.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancellation
            result = execution.result()
            interrupts = _interrupts_payload(getattr(result, "interrupts", ()))
            async with connect() as conn:
                run_row = await conn.session.get(RunRow, run_id)
                thread_row = await conn.session.get(ThreadRow, thread_id) if thread_id else None
                if run_row is None or is_terminal(run_row.status):
                    return True
                if interrupts:
                    metric_inc("graphharbor_runs_interrupted_total", labels={"reason": "hitl"})
                    if thread_row is not None:
                        thread_row.status = "interrupted"
                        thread_row.interrupts = {item["id"]: item for item in interrupts}
                        thread_row.values_ = _jsonable(getattr(result, "value", None))
                    await self.repository.finish(
                        conn.session,
                        run_id,
                        self.owner,
                        RunStatus.INTERRUPTED,
                        reason=RunReason.HITL_INTERRUPT,
                    )
                else:
                    metric_inc("graphharbor_runs_completed_total")
                    if thread_row is not None:
                        thread_row.status = "idle"
                        thread_row.interrupts = {}
                        thread_row.values_ = _jsonable(getattr(result, "value", result))
                        thread_row.error = None
                    await self.repository.finish(
                        conn.session,
                        run_id,
                        self.owner,
                        RunStatus.SUCCESS,
                        reason=RunReason.COMPLETED,
                    )
            if interrupts:
                for interrupt in interrupts:
                    await self._publish_event(
                        run_id,
                        thread_id,
                        {
                            "event": "input.requested",
                            "namespace": interrupt.get("ns") or [],
                            "data": {
                                "interrupt_id": interrupt["id"],
                                "value": interrupt["value"],
                            },
                        },
                    )
            await self._publish_event(
                run_id,
                thread_id,
                {
                    "event": "lifecycle",
                    "status": RunStatus.INTERRUPTED.value
                    if interrupts
                    else RunStatus.SUCCESS.value,
                    "reason": (
                        RunReason.HITL_INTERRUPT.value if interrupts else RunReason.COMPLETED.value
                    ),
                    "output": _jsonable(getattr(result, "value", result)),
                    "interrupts": interrupts,
                },
            )
        except RunCancelled:
            publish_cancel_event = False
            if self.stop_event.is_set():
                async with connect() as conn:
                    await self.repository.requeue_for_shutdown(conn.session, run_id, self.owner)
                lifecycle_status = RunStatus.PENDING.value
                lifecycle_reason = RunReason.SHUTDOWN_REQUEUE.value
            else:
                metric_inc("graphharbor_runs_interrupted_total", labels={"reason": "cancel"})
                async with connect() as conn:
                    run_row = await conn.session.get(RunRow, run_id)
                    if run_row is not None and not is_terminal(run_row.status):
                        if thread_id:
                            thread_row = await conn.session.get(ThreadRow, thread_id)
                            if thread_row is not None:
                                thread_row.status = "idle"
                        await self.repository.finish(
                            conn.session,
                            run_id,
                            self.owner,
                            RunStatus.INTERRUPTED,
                            reason=RunReason.CANCEL_REQUESTED,
                        )
                        publish_cancel_event = True
                lifecycle_status = RunStatus.INTERRUPTED.value
                lifecycle_reason = RunReason.CANCEL_REQUESTED.value
            if publish_cancel_event or self.stop_event.is_set():
                with contextlib.suppress(Exception):
                    await self._publish_event(
                        run_id,
                        thread_id,
                        {
                            "event": "lifecycle",
                            "status": lifecycle_status,
                            "reason": lifecycle_reason,
                        },
                    )
        except Exception as exc:
            infrastructure = _is_infrastructure_error(exc)
            metric_inc(
                "graphharbor_runs_failed_total",
                labels={"kind": "infrastructure" if infrastructure else "business"},
            )
            async with connect() as conn:
                run_row = await conn.session.get(RunRow, run_id)
                if run_row is not None and not is_terminal(run_row.status):
                    if thread_id:
                        thread_row = await conn.session.get(ThreadRow, thread_id)
                        if thread_row is not None:
                            thread_row.status = "idle"
                            thread_row.error = {"message": str(exc), "type": type(exc).__name__}
                    await self.repository.fail(
                        conn.session,
                        run_id,
                        self.owner,
                        infrastructure=infrastructure,
                        reason=(
                            RunReason.INFRASTRUCTURE_ERROR
                            if infrastructure
                            else RunReason.BUSINESS_ERROR
                        ),
                    )
            if infrastructure:
                try:
                    self.registry.attach_checkpointer(await reconnect_checkpointer())
                except Exception:
                    logger.exception("checkpointer reconnect failed")
            with contextlib.suppress(Exception):
                await self._publish_event(
                    run_id,
                    thread_id,
                    {
                        "event": "lifecycle",
                        "status": RunStatus.ERROR.value,
                        "error": {"type": type(exc).__name__, "message": str(exc)},
                    },
                )
            logger.exception("run execution failed", run_id=str(run_id), graph_id=graph_id)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await clear_run_heartbeat(run_id)
        return True

    async def reap_once(self) -> int:
        """Reclaim expired PostgreSQL leases independently of queue traffic."""
        async with connect() as conn:
            count = await self.repository.requeue_expired(conn.session)
            events = list(self.repository.last_requeued_events)
        for event in events:
            await self._fanout_durable_event(event)
        if count:
            metric_inc("graphharbor_runs_requeued_total", count)
            await wake_run_queue()
        return count

    async def run_forever(self) -> None:
        reaper: asyncio.Task | None = None
        try:
            reaper = asyncio.create_task(self._reaper_loop(), name=f"reaper-{self.owner}")
            while not self.stop_event.is_set():
                try:
                    did_work = await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    metric_inc("graphharbor_worker_loop_failures_total")
                    logger.exception("worker loop failed; retrying")
                    await asyncio.sleep(1)
                    continue
                if not did_work:
                    await wait_for_queue_wake(timeout=0.5)
        finally:
            if reaper is not None:
                reaper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reaper

    async def _reaper_loop(self) -> None:
        try:
            interval = max(float(os.environ.get("GRAPHHARBOR_REAPER_INTERVAL_SECONDS", "5")), 0.5)
        except ValueError:
            interval = 5.0
        while not self.stop_event.is_set():
            await asyncio.sleep(interval)
            try:
                await self.reap_once()
            except Exception:
                logger.exception("lease reaper failed")


async def run_worker(config_path: Path) -> None:
    configure_structured_logging()
    registry = GraphRegistry.from_path(config_path)
    previous_auto_migrate = os.environ.get("LG_RUNTIME_PG_AUTO_MIGRATE")
    os.environ["LG_RUNTIME_PG_AUTO_MIGRATE"] = "false"
    await start_pool()
    worker = ProductionWorker(registry)
    registry.attach_checkpointer(get_checkpointer())
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.stop_event.set)
            installed_signals.append(sig)
        except (NotImplementedError, RuntimeError):
            continue
    try:
        await worker.run_forever()
    finally:
        for sig in installed_signals:
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
        await stop_pool()
        if previous_auto_migrate is None:
            os.environ.pop("LG_RUNTIME_PG_AUTO_MIGRATE", None)
        else:
            os.environ["LG_RUNTIME_PG_AUTO_MIGRATE"] = previous_auto_migrate


__all__ = ["ProductionWorker", "RunCancelled", "run_worker"]
