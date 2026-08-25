"""Official Agent Server run SSE adapter."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from langgraph_runtime_pg.auth import in_principal_scope, principal_from_scope
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import RunRow, RuntimeEventRow, ThreadRow
from langgraph_runtime_pg.protocol import RunStatus, project_v3_event
from langgraph_runtime_pg.redis_stream import Message, get_stream_manager

_TERMINAL = frozenset(
    {
        RunStatus.SUCCESS.value,
        RunStatus.ERROR.value,
        RunStatus.TIMEOUT.value,
        RunStatus.INTERRUPTED.value,
    }
)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _sse(
    event: str,
    data: Any = None,
    *,
    event_id: int | str | None = None,
    event_id_last: bool = False,
) -> str:
    lines: list[str] = []
    if event_id is not None and not event_id_last:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    if data is not None:
        encoded = json.dumps(_jsonable(data), ensure_ascii=False, separators=(",", ":"))
        lines.extend(f"data: {line}" for line in encoded.splitlines() or [encoded])
    if event_id is not None and event_id_last:
        lines.append(f"id: {event_id}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _stream_modes(payload: dict[str, Any]) -> set[str]:
    value = payload.get("stream_mode", "values")
    if isinstance(value, str):
        values = {value}
    elif isinstance(value, (list, tuple, set)):
        values = {str(item) for item in value}
    else:
        values = {"values"}
    aliases = {
        "messages-tuple": "messages",
        "messages": "messages",
        "events": "events",
    }
    return values | {aliases[item] for item in values if item in aliases}


def _event_envelope(row: RuntimeEventRow) -> dict[str, Any]:
    return {
        "id": str(row.event_id),
        "seq": row.sequence,
        "run_id": str(row.run_id) if row.run_id else None,
        "thread_id": str(row.thread_id) if row.thread_id else None,
        "event": row.payload,
        "namespace": list(row.namespace or []),
    }


_REDIS_STREAM_ID = re.compile(r"^\d+-\d+$")


def _thread_stream_modes(request: Request) -> set[str] | JSONResponse:
    raw = request.query_params.get("stream_modes")
    modes = {item.strip() for item in raw.split(",")} if raw else {"run_modes"}
    invalid = modes - {"lifecycle", "run_modes", "state_update"}
    if invalid:
        return JSONResponse({"detail": f"Invalid stream mode: {sorted(invalid)[0]}"}, status_code=422)
    return modes


def _thread_cursor(value: str | None) -> int | JSONResponse:
    if value is None:
        return -1
    if value == "-":
        return 0
    if not _REDIS_STREAM_ID.fullmatch(value):
        return JSONResponse(
            {"detail": "Invalid last-event-id: must be a valid Redis stream ID"}, status_code=422
        )
    return int(value.partition("-")[0])


async def _thread_events(thread_id: UUID, after: int, principal: Any) -> list[RuntimeEventRow]:
    query = (
        select(RuntimeEventRow)
        .where(RuntimeEventRow.thread_id == thread_id, RuntimeEventRow.sequence > after)
        .order_by(RuntimeEventRow.sequence)
    )
    if principal is not None:
        query = query.join(ThreadRow, ThreadRow.thread_id == RuntimeEventRow.thread_id).where(
            ThreadRow.tenant_id == principal.tenant_id,
            ThreadRow.project_id == principal.project_id,
        )
    async with connect() as conn:
        return list((await conn.session.execute(query)).scalars())


async def _thread_event_sequence(thread_id: UUID, principal: Any) -> int:
    query = select(func.coalesce(func.max(RuntimeEventRow.sequence), 0)).where(
        RuntimeEventRow.thread_id == thread_id
    )
    if principal is not None:
        query = query.join(ThreadRow, ThreadRow.thread_id == RuntimeEventRow.thread_id).where(
            ThreadRow.tenant_id == principal.tenant_id,
            ThreadRow.project_id == principal.project_id,
        )
    async with connect() as conn:
        return int(await conn.session.scalar(query) or 0)


async def _thread_frame(row: RuntimeEventRow, modes: set[str]) -> tuple[str, Any, str] | None:
    event = row.payload
    name = str(event.get("event") or event.get("method") or "custom")
    if name == "lifecycle":
        status = str(event.get("status") or "")
        if status == RunStatus.RUNNING.value:
            if "lifecycle" not in modes and "run_modes" not in modes:
                return None
            attempt = 1
            if row.run_id is not None:
                async with connect() as conn:
                    run = await conn.session.get(RunRow, row.run_id)
                if run is not None:
                    attempt = max(run.retry_count, 1)
            return "metadata", {"run_id": str(row.run_id), "attempt": attempt}, f"{row.sequence}-0"
        if status in _TERMINAL:
            if "lifecycle" not in modes and "run_modes" not in modes:
                return None
            return "metadata", {"status": "run_done", "run_id": str(row.run_id)}, f"{row.sequence}-0"
        return None
    if name == "state_update":
        if "state_update" not in modes:
            return None
    elif "run_modes" not in modes:
        return None
    return name, event.get("data"), f"{row.sequence}-0"


async def thread_stream(request: Request) -> JSONResponse | StreamingResponse:
    try:
        thread_id = UUID(str(request.path_params["thread_id"]))
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"detail": "Invalid thread ID: must be a UUID"}, status_code=422)
    modes = _thread_stream_modes(request)
    if isinstance(modes, JSONResponse):
        return modes
    cursor = _thread_cursor(request.headers.get("last-event-id"))
    if isinstance(cursor, JSONResponse):
        return cursor
    principal = principal_from_scope(request.scope)
    heartbeat = max(float(os.environ.get("GRAPHHARBOR_THREAD_STREAM_HEARTBEAT_SECONDS", "15")), 0.1)
    manager = get_stream_manager()

    async def body() -> AsyncIterator[str]:
        nonlocal cursor
        queue = await manager.add_thread_stream(thread_id)
        try:
            if cursor < 0:
                cursor = await _thread_event_sequence(thread_id, principal)
            while True:
                for row in await _thread_events(thread_id, cursor, principal):
                    cursor = row.sequence
                    frame = await _thread_frame(row, modes)
                    if frame is not None:
                        name, data, event_id = frame
                        yield _sse(name, data, event_id=event_id, event_id_last=True)
                try:
                    await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": heartbeat\n\n"
        finally:
            await manager.remove_thread_stream(thread_id, queue)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _message_envelope(message: Message) -> dict[str, Any] | None:
    try:
        value = json.loads(message.data)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _event_frame(
    envelope: dict[str, Any],
    *,
    modes: set[str],
    stream_subgraphs: bool,
    version: str = "v2",
) -> tuple[str, Any, int] | None:
    event = envelope.get("event")
    if not isinstance(event, dict):
        return None
    typed = project_v3_event(
        event,
        sequence=int(envelope.get("seq", 0) or 0),
        fallback_namespace=list(envelope.get("namespace") or []),
    )
    name = str(typed["method"])
    typed_params = typed["params"]
    namespace = list(typed_params.get("namespace") or [])
    if namespace and not stream_subgraphs:
        return None
    if name == "lifecycle" and version != "v3":
        return None
    if version != "v3" and name not in modes and "events" not in modes and "debug" not in modes:
        return None
    data = typed_params.get("data")
    interrupts = typed_params.get("interrupts") or event.get("interrupts") or ()
    if name == "values" and interrupts:
        if isinstance(data, dict):
            data = {**data, "__interrupt__": _jsonable(interrupts)}
        else:
            data = {"value": data, "__interrupt__": _jsonable(interrupts)}
    try:
        sequence = int(envelope["seq"])
    except (KeyError, TypeError, ValueError):
        return None
    if version == "v3":
        typed["seq"] = sequence
        event_name = name
    else:
        event_name = "|".join([name, *namespace]) if namespace else name
    return event_name, (typed if version == "v3" else data), sequence


async def _load_events(run_id: UUID, *, after: int = 0) -> list[dict[str, Any]]:
    async with connect() as conn:
        query = (
            select(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id, RuntimeEventRow.sequence > after)
            .order_by(RuntimeEventRow.sequence)
        )
        rows = (await conn.session.execute(query)).scalars().all()
    return [_event_envelope(row) for row in rows]


async def _run_snapshot(run_id: UUID, principal: Any) -> RunRow | None:
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
    if row is None or not in_principal_scope(row, principal):
        return None
    return row


async def _run_sse(
    request: Request,
    *,
    run_id: UUID,
    thread_id: UUID | None,
    payload: dict[str, Any],
    include_location: bool,
    version: str = "v2",
) -> StreamingResponse:
    principal = principal_from_scope(request.scope)
    modes = _stream_modes(payload)
    stream_subgraphs = bool(payload.get("stream_subgraphs", False))
    try:
        cursor = max(int(request.headers.get("last-event-id", "0") or "0"), 0)
    except ValueError:
        cursor = 0
    heartbeat = max(float(os.environ.get("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "15")), 0.1)
    timeout = max(float(os.environ.get("GRAPHHARBOR_SSE_TIMEOUT_SECONDS", "300")), heartbeat)
    resumable = bool(payload.get("stream_resumable", False)) or "last-event-id" in request.headers
    manager = get_stream_manager()

    async def body() -> AsyncIterator[str]:
        metric_inc("graphharbor_sse_connections_opened_total", labels={"version": version})
        if cursor:
            metric_inc("graphharbor_sse_reconnects_total", labels={"version": version})
        queue = await manager.add_queue(run_id, thread_id, replay=True)
        seen: set[int] = set()
        try:
            snapshot = await _run_snapshot(run_id, principal)
            if snapshot is None:
                yield _sse("error", {"detail": "run not found"})
                return
            yield _sse(
                "metadata",
                {"run_id": str(run_id), "attempt": snapshot.retry_count + 1},
            )

            async def emit_envelope(envelope: dict[str, Any]) -> AsyncIterator[str]:
                frame = _event_frame(
                    envelope,
                    modes=modes,
                    stream_subgraphs=stream_subgraphs,
                    version=version,
                )
                if frame is None:
                    return
                name, data, sequence = frame
                if sequence <= cursor or sequence in seen:
                    return
                seen.add(sequence)
                metric_inc("graphharbor_sse_events_total", labels={"version": version})
                yield _sse(name, data, event_id=sequence if resumable else None)

            for envelope in await _load_events(run_id, after=cursor):
                async for frame in emit_envelope(envelope):
                    yield frame
            snapshot = await _run_snapshot(run_id, principal)
            if snapshot is None or snapshot.status in _TERMINAL:
                return

            started = asyncio.get_running_loop().time()
            while asyncio.get_running_loop().time() - started < timeout:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=heartbeat)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    snapshot = await _run_snapshot(run_id, principal)
                    if snapshot is None:
                        yield _sse("error", {"detail": "run not found"})
                        return
                    if snapshot.status in _TERMINAL:
                        for envelope in await _load_events(run_id, after=max(seen or {cursor})):
                            async for frame in emit_envelope(envelope):
                                yield frame
                        return
                    continue
                live_envelope = _message_envelope(message)
                if live_envelope is None:
                    continue
                async for frame in emit_envelope(live_envelope):
                    yield frame
                event = live_envelope.get("event")
                if isinstance(event, dict) and event.get("event") == "lifecycle":
                    status = str(event.get("status", ""))
                    if status in _TERMINAL:
                        return
            yield _sse("error", {"detail": "stream timed out"})
        finally:
            metric_inc("graphharbor_sse_connections_closed_total", labels={"version": version})
            await manager.remove_queue(run_id, thread_id, queue)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    if include_location:
        suffix = (
            f"/threads/{thread_id}/runs/{run_id}/stream" if thread_id else f"/runs/{run_id}/stream"
        )
        headers["Location"] = suffix
        # The official Python SDK reads Content-Location for on_run_created.
        headers["Content-Location"] = suffix.removesuffix("/stream")
    return StreamingResponse(body(), media_type="text/event-stream", headers=headers)


async def runs_stream(
    request: Request, *, thread_value: str | None = None
) -> JSONResponse | StreamingResponse:
    from langhost.core_api import runs_create

    thread_value = thread_value or request.path_params.get("thread_id")
    payload = await request.json()
    version = str(payload.get("version", "v2"))
    if version not in {"v2", "v3"}:
        return JSONResponse(
            {"detail": "GraphHarbor currently supports runs.stream version='v2' or 'v3'"},
            status_code=422,
        )
    created = await runs_create(request, thread_value=thread_value, payload=payload)
    if created.status_code >= 300:
        return created
    try:
        raw = json.loads(bytes(created.body))
        run_id = UUID(str(raw["run_id"]))
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return JSONResponse({"detail": "run creation returned an invalid run"}, status_code=500)
    thread_id = UUID(thread_value) if thread_value else None
    return await _run_sse(
        request,
        run_id=run_id,
        thread_id=thread_id,
        payload=payload,
        include_location=True,
        version=version,
    )


async def runs_stream_existing(request: Request) -> JSONResponse | StreamingResponse:
    try:
        run_id = UUID(str(request.path_params["run_id"]))
        thread_value = request.path_params.get("thread_id")
        thread_id = UUID(str(thread_value)) if thread_value else None
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"detail": "run not found"}, status_code=404)
    principal = principal_from_scope(request.scope)
    row = await _run_snapshot(run_id, principal)
    if row is None or row.thread_id != thread_id:
        return JSONResponse({"detail": "run not found"}, status_code=404)
    return await _run_sse(
        request,
        run_id=run_id,
        thread_id=thread_id,
        payload=row.kwargs,
        include_location=False,
        version=str(row.kwargs.get("version", "v2")),
    )


__all__ = ["runs_stream", "runs_stream_existing", "thread_stream"]
