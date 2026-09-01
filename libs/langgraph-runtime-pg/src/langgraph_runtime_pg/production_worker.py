"""Small production worker loop built on public LangGraph APIs."""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import os
import signal
import socket
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from langgraph_runtime_pg.auth import (
    RuntimeContextError,
    validate_policy_overrides,
    verify_runtime_context_envelope,
)
from langgraph_runtime_pg.checkpoint import get_checkpointer, reconnect_checkpointer
from langgraph_runtime_pg.database import connect, start_pool, stop_pool
from langgraph_runtime_pg.graph_executor import (
    invoke_graph,
    normalize_durability,
    normalize_interrupt_nodes,
    resume_command,
    thread_config,
)
from langgraph_runtime_pg.graph_registry import GraphRegistry
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
from langgraph_runtime_pg.production import configure_structured_logging
from langgraph_runtime_pg.protocol import (
    TERMINAL_RUN_STATUSES,
    RunReason,
    RunStatus,
    protocol_event,
)
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
from langgraph_runtime_pg.run_store import RunOwnershipError, RunRepository
from langgraph_runtime_pg.thread_config import attach_thread_metadata

logger = structlog.stdlib.get_logger(__name__)


class RunCancelled(Exception):
    pass


class RunTimedOut(Exception):
    pass


def _run_timeout_seconds() -> float | None:
    raw = os.environ.get("GRAPHHARBOR_RUN_TIMEOUT_SECONDS")
    if raw is None or not raw.strip():
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("GRAPHHARBOR_RUN_TIMEOUT_SECONDS must be a positive number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("GRAPHHARBOR_RUN_TIMEOUT_SECONDS must be a positive number")
    return value


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
        try:
            lease_seconds = max(int(os.environ.get("GRAPHHARBOR_LEASE_SECONDS", "60")), 5)
        except ValueError:
            lease_seconds = 60
        self.repository = RunRepository(lease_seconds=lease_seconds)
        self.run_timeout_seconds = _run_timeout_seconds()
        self.stop_event = asyncio.Event()

    async def _publish_event(
        self,
        run_id: UUID,
        thread_id: UUID | None,
        event: dict[str, Any],
        *,
        trace_context: dict[str, Any] | None = None,
    ) -> None:
        topic = str(event.get("event", "custom"))
        status = event.get("status")
        terminal = topic == "lifecycle" and str(status) in TERMINAL_RUN_STATUSES
        async with connect() as conn:
            kwargs: dict[str, Any] = {
                "run_id": run_id,
                "thread_id": thread_id,
                "topic": topic,
                "payload": event,
                "namespace": list(event.get("namespace") or event.get("parent_ids") or []),
                "trace_context": trace_context,
            }
            if terminal:
                kwargs["terminal"] = True
            durable = await self.repository.record_event(conn.session, **kwargs)
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
        interval = min(
            max(bg_job_heartbeat_secs() / 2.0, 1.0),
            max(self.repository.lease_seconds / 3.0, 1.0),
        )
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
        graph_id = ""
        runtime_context_error: RuntimeContextError | None = None
        claimed_transition_events: list[Any] = []
        async with connect() as conn:
            run = await self.repository.claim_next(conn.session, self.owner)
            if run is None:
                claimed_transition_events = list(self.repository.last_transition_events)
            if run is None:
                pass
            else:
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
                    claimed_transition_events = list(self.repository.last_transition_events)
                    run = None
                else:
                    graph_id = assistant.graph_id
                    command = resume_command(run.kwargs.get("command"))
                    durability = normalize_durability(run.kwargs.get("durability"))
                    interrupt_before = normalize_interrupt_nodes(
                        run.kwargs.get("interrupt_before"), "interrupt_before"
                    )
                    interrupt_after = normalize_interrupt_nodes(
                        run.kwargs.get("interrupt_after"), "interrupt_after"
                    )
                    input_value = command or run.kwargs.get("input", run.kwargs)
                    run_config = run.kwargs.get("config")
                    if not isinstance(run_config, dict):
                        run_config = {}
                    config_sources = [
                        item
                        for item in (
                            assistant.config,
                            thread.config if thread else None,
                            run_config,
                        )
                        if isinstance(item, dict)
                    ]
                    configurable = {}
                    source_metadata: dict[str, Any] = {}
                    tag_values: list[str] = []
                    for source in config_sources:
                        source_configurable = source.get("configurable")
                        if isinstance(source_configurable, dict):
                            configurable.update(source_configurable)
                        source_metadata_value = source.get("metadata")
                        if isinstance(source_metadata_value, dict):
                            source_metadata.update(source_metadata_value)
                        source_tags = source.get("tags")
                        if isinstance(source_tags, (list, tuple)):
                            tag_values.extend(item for item in source_tags if isinstance(item, str))
                    configurable = {
                        key: value
                        for key, value in configurable.items()
                        if key
                        not in {
                            "thread_id",
                            "run_id",
                            "tenant_id",
                            "project_id",
                            "user_id",
                            "role",
                            "permissions",
                            "__pregel_runtime",
                            "__graphharbor_runtime_context",
                        }
                    }
                    checkpoint_id = run.kwargs.get("checkpoint_id")
                    if isinstance(checkpoint_id, str) and checkpoint_id:
                        configurable["checkpoint_id"] = checkpoint_id
                    merged_context: dict[str, Any] = {}
                    for context_source in (
                        assistant.context,
                        thread.config.get("context")
                        if thread and isinstance(thread.config, dict)
                        else None,
                        run.kwargs.get("context"),
                    ):
                        if isinstance(context_source, dict):
                            merged_context.update(context_source)
                    run_context: dict[str, Any] | None = merged_context or None
                    runtime_context = None
                    runtime_policy = None
                    runtime_context_token = run.kwargs.get("runtime_context_token")
                    if isinstance(runtime_context_token, str):
                        try:
                            runtime_context, runtime_policy = verify_runtime_context_envelope(
                                runtime_context_token,
                                run_id=str(run.run_id),
                                thread_id=str(thread_id) if thread_id else None,
                                tenant_id=run.tenant_id or (thread.tenant_id if thread else None),
                                project_id=run.project_id
                                or (thread.project_id if thread else None),
                            )
                        except RuntimeContextError as exc:
                            runtime_context_error = exc
                    elif os.environ.get("GRAPHHARBOR_ENV", "development") == "production":
                        runtime_context_error = RuntimeContextError(
                            "signed runtime context is required in production"
                        )
                    else:
                        runtime_context = {
                            "user_id": "anonymous",
                            "tenant_id": run.tenant_id
                            or (thread.tenant_id if thread else None)
                            or "__anonymous__",
                            "project_id": run.project_id
                            or (thread.project_id if thread else None)
                            or "__acceptance__",
                            "role": "anonymous",
                            "permissions": [],
                        }
                    if runtime_context_error is None:
                        try:
                            validate_policy_overrides(
                                runtime_policy,
                                configurable=configurable,
                                context=run_context,
                            )
                        except RuntimeContextError as exc:
                            runtime_context_error = exc
                    metadata: dict[str, Any] = {}
                    if isinstance(assistant.metadata_, dict):
                        metadata.update(assistant.metadata_)
                    if thread is not None and isinstance(thread.metadata_, dict):
                        metadata.update(thread.metadata_)
                    if isinstance(run.metadata_, dict):
                        metadata.update(run.metadata_)
                    metadata.update(source_metadata)
                    if isinstance(run.kwargs.get("metadata"), dict):
                        metadata.update(run.kwargs["metadata"])
                    metadata.update(
                        {
                            "run_id": str(run.run_id),
                            "thread_id": str(thread_id) if thread_id else None,
                            "assistant_id": str(run.assistant_id),
                            "assistant_version": assistant.version,
                        }
                    )
                    if thread is not None and isinstance(thread.metadata_, dict):
                        attach_thread_metadata(metadata, thread.metadata_)
                    metadata = {key: value for key, value in metadata.items() if value is not None}
                    tags = list(dict.fromkeys(tag_values)) or None
                    trace_context = {
                        "assistant_id": str(run.assistant_id),
                        "assistant_version": str(assistant.version),
                        "graph_id": str(graph_id),
                        "model_id": str(configurable.get("model_id") or ""),
                        "tenant_id": str(runtime_context.get("tenant_id") or "")
                        if runtime_context
                        else None,
                        "project_id": str(runtime_context.get("project_id") or "")
                        if runtime_context
                        else None,
                        "user_id": str(runtime_context.get("user_id") or "")
                        if runtime_context
                        else None,
                        "policy_version": runtime_policy.version if runtime_policy else None,
                    }
                    trace_context = {key: value for key, value in trace_context.items() if value}
                    if thread is not None:
                        thread.status = "busy"
                        await conn.session.flush()

        for event in claimed_transition_events:
            await self._fanout_durable_event(event)
        if run is None:
            return bool(claimed_transition_events)
        if runtime_context_error is not None:
            invalid_events: list[Any] = []
            async with connect() as conn:
                failed = await self.repository.fail(
                    conn.session,
                    run_id,
                    self.owner,
                    infrastructure=False,
                    reason=RunReason.BUSINESS_ERROR,
                    terminal_payload={
                        "event": "lifecycle",
                        "status": RunStatus.ERROR.value,
                        "reason": RunReason.BUSINESS_ERROR.value,
                        "error": {
                            "type": type(runtime_context_error).__name__,
                            "message": str(runtime_context_error),
                        },
                    },
                )
                del failed
                invalid_events = list(self.repository.last_transition_events)
            for event in invalid_events:
                await self._fanout_durable_event(event)
            return True

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
                trace_context=trace_context,
            )
            if await self._cancel_requested(run_id, thread_id):
                raise RunCancelled

            async def on_event(event: dict[str, Any]) -> None:
                if self.stop_event.is_set():
                    raise RunCancelled
                if await self._cancel_requested(run_id, thread_id):
                    raise RunCancelled
                await self._publish_event(run_id, thread_id, event)

            config = thread_config(
                str(thread_id) if thread_id else None,
                assistant_id=str(run.assistant_id),
                graph_id=str(graph_id),
                configurable=configurable,
                metadata=metadata,
                tags=tags,
                context=run_context,
                runtime_context=runtime_context,
                runtime_policy=(
                    {
                        "version": runtime_policy.version,
                        "allowed_model_ids": list(runtime_policy.allowed_model_ids),
                        "allowed_tool_names": list(runtime_policy.allowed_tool_names),
                    }
                    if runtime_policy
                    else None
                ),
            )

            async def execute() -> Any:
                async with self.registry.open(graph_id, config) as graph:
                    return await invoke_graph(
                        graph,
                        input_value,
                        config=config,
                        on_event=on_event,
                        durability=durability,
                        interrupt_before=interrupt_before,
                        interrupt_after=interrupt_after,
                    )

            execution = asyncio.create_task(
                execute(),
                name=f"run-{run_id}",
            )
            cancellation = asyncio.create_task(cancel_event.wait(), name=f"cancel-{run_id}")
            done, _ = await asyncio.wait(
                {execution, cancellation},
                timeout=self.run_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                execution.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await execution
                cancellation.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cancellation
                raise RunTimedOut("graphharbor.run_timeout")
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
            completed_events: list[Any] = []
            async with connect() as conn:
                run_row = await conn.session.get(RunRow, run_id)
                thread_row = await conn.session.get(ThreadRow, thread_id) if thread_id else None
                if run_row is None or is_terminal(run_row.status):
                    return True
                preceding_events = [
                    (
                        "input.requested",
                        {
                            "event": "input.requested",
                            "namespace": interrupt.get("ns") or [],
                            "data": {
                                "interrupt_id": interrupt["id"],
                                "value": interrupt["value"],
                            },
                        },
                        interrupt.get("ns") or [],
                    )
                    for interrupt in interrupts
                ]
                terminal_status = (
                    RunStatus.INTERRUPTED.value if interrupts else RunStatus.SUCCESS.value
                )
                terminal_reason = (
                    RunReason.HITL_INTERRUPT.value if interrupts else RunReason.COMPLETED.value
                )
                terminal_payload = {
                    "event": "lifecycle",
                    "status": terminal_status,
                    "reason": terminal_reason,
                    "output": _jsonable(getattr(result, "value", result)),
                    "interrupts": interrupts,
                }
                if interrupts:
                    finished = await self.repository.finish(
                        conn.session,
                        run_id,
                        self.owner,
                        RunStatus.INTERRUPTED,
                        reason=RunReason.HITL_INTERRUPT,
                        terminal_payload=terminal_payload,
                        preceding_events=preceding_events,
                        trace_context=trace_context,
                    )
                    if finished is None:
                        return True
                    metric_inc("graphharbor_runs_interrupted_total", labels={"reason": "hitl"})
                    if thread_row is not None:
                        thread_row.status = "interrupted"
                        thread_row.interrupts = {item["id"]: item for item in interrupts}
                        thread_row.values_ = _jsonable(getattr(result, "value", None))
                else:
                    finished = await self.repository.finish(
                        conn.session,
                        run_id,
                        self.owner,
                        RunStatus.SUCCESS,
                        reason=RunReason.COMPLETED,
                        terminal_payload=terminal_payload,
                        trace_context=trace_context,
                    )
                    if finished is None:
                        return True
                    metric_inc("graphharbor_runs_completed_total")
                    if thread_row is not None:
                        thread_row.status = "idle"
                        thread_row.interrupts = {}
                        thread_row.values_ = _jsonable(getattr(result, "value", result))
                        thread_row.error = None
                completed_events = list(self.repository.last_transition_events)
            for event in completed_events:
                await self._fanout_durable_event(event)
        except RunCancelled:
            publish_cancel_event = False
            cancel_events: list[Any] = []
            if self.stop_event.is_set():
                async with connect() as conn:
                    await self.repository.requeue_for_shutdown(conn.session, run_id, self.owner)
                    cancel_events = list(self.repository.last_transition_events)
                lifecycle_status = RunStatus.PENDING.value
                lifecycle_reason = RunReason.SHUTDOWN_REQUEUE.value
            else:
                metric_inc("graphharbor_runs_interrupted_total", labels={"reason": "cancel"})
                async with connect() as conn:
                    run_row = await conn.session.get(RunRow, run_id)
                    if run_row is not None and not is_terminal(run_row.status):
                        finished = await self.repository.finish(
                            conn.session,
                            run_id,
                            self.owner,
                            RunStatus.INTERRUPTED,
                            reason=RunReason.CANCEL_REQUESTED,
                            terminal_payload={
                                "event": "lifecycle",
                                "status": RunStatus.INTERRUPTED.value,
                                "reason": RunReason.CANCEL_REQUESTED.value,
                            },
                            trace_context=trace_context,
                        )
                        if finished is None:
                            return True
                        if thread_id:
                            thread_row = await conn.session.get(ThreadRow, thread_id)
                            if thread_row is not None:
                                thread_row.status = "idle"
                        publish_cancel_event = True
                        cancel_events = list(self.repository.last_transition_events)
                lifecycle_status = RunStatus.INTERRUPTED.value
                lifecycle_reason = RunReason.CANCEL_REQUESTED.value
            if cancel_events:
                for event in cancel_events:
                    await self._fanout_durable_event(event)
            elif publish_cancel_event or self.stop_event.is_set():
                with contextlib.suppress(Exception):
                    await self._publish_event(
                        run_id,
                        thread_id,
                        {
                            "event": "lifecycle",
                            "status": lifecycle_status,
                            "reason": lifecycle_reason,
                        },
                        trace_context=trace_context,
                    )
        except RunOwnershipError:
            logger.info("run finalization lost lease ownership", run_id=str(run_id))
        except Exception as exc:
            timed_out = isinstance(exc, RunTimedOut)
            infrastructure = not timed_out and _is_infrastructure_error(exc)
            metric_inc(
                "graphharbor_runs_failed_total",
                labels={
                    "kind": (
                        "timeout"
                        if timed_out
                        else "infrastructure"
                        if infrastructure
                        else "business"
                    )
                },
            )
            failure_events: list[Any] = []
            async with connect() as conn:
                run_row = await conn.session.get(RunRow, run_id)
                if run_row is not None and not is_terminal(run_row.status):
                    if thread_id:
                        thread_row = await conn.session.get(ThreadRow, thread_id)
                        if thread_row is not None:
                            thread_row.status = "idle"
                            thread_row.error = {"message": str(exc), "type": type(exc).__name__}
                    retrying = infrastructure and run_row.retry_count < min(
                        run_row.max_attempts, self.repository.max_attempts
                    )
                    if timed_out:
                        await self.repository.finish(
                            conn.session,
                            run_id,
                            self.owner,
                            RunStatus.TIMEOUT,
                            reason=RunReason.TIMEOUT,
                            terminal_payload={
                                "event": "lifecycle",
                                "status": RunStatus.TIMEOUT.value,
                                "reason": RunReason.TIMEOUT.value,
                                "error": {"type": type(exc).__name__, "message": str(exc)},
                            },
                            trace_context=trace_context,
                        )
                    else:
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
                            terminal_payload=None
                            if retrying
                            else {
                                "event": "lifecycle",
                                "status": RunStatus.ERROR.value,
                                "reason": (
                                    RunReason.INFRASTRUCTURE_ERROR.value
                                    if infrastructure
                                    else RunReason.BUSINESS_ERROR.value
                                ),
                                "error": {"type": type(exc).__name__, "message": str(exc)},
                            },
                            trace_context=trace_context,
                        )
                    failure_events = list(self.repository.last_transition_events)
            if infrastructure:
                try:
                    self.registry.attach_checkpointer(await reconnect_checkpointer())
                except Exception:
                    logger.exception("checkpointer reconnect failed")
            for event in failure_events:
                with contextlib.suppress(Exception):
                    await self._fanout_durable_event(event)
            logger.exception("run execution failed", run_id=str(run_id), graph_id=graph_id or "")
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
