"""Thread-centric Agent Protocol transport used by the official SDK."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from langgraph_runtime_pg.auth import principal_from_scope, scoped_idempotency_key
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.metrics import inc as metric_inc
from langgraph_runtime_pg.models import RunRow, RuntimeEventRow, ThreadRow
from langgraph_runtime_pg.protocol import protocol_event
from langgraph_runtime_pg.redis_stream import get_stream_manager


def _error(
    command_id: Any,
    code: str,
    message: str,
    status: int = 200,
    *,
    applied_through_seq: int | None = None,
) -> JSONResponse:
    meta = {} if applied_through_seq is None else {"applied_through_seq": applied_through_seq}
    return JSONResponse(
        {"id": command_id, "type": "error", "error": code, "message": message, "meta": meta},
        status_code=status,
    )


def _command_id(body: dict[str, Any]) -> Any:
    return body.get("id")


async def _thread(request: Request, thread_id: UUID, action: str = "read") -> ThreadRow | None:
    from langhost.core_api import _get_thread

    row, _, actual_id = await _get_thread(request, action=action)
    return row if actual_id == thread_id else None


async def _latest_run(thread_id: UUID, request: Request) -> RunRow | None:
    from langhost.core_api import _authorized_run

    async with connect() as conn:
        query = (
            select(RunRow)
            .where(RunRow.thread_id == thread_id)
            .order_by(RunRow.created_at.desc())
            .limit(1)
        )
        row = (await conn.session.execute(query)).scalar_one_or_none()
        return (
            await _authorized_run(request, conn.session, row.run_id, "update")
            if row is not None
            else None
        )


async def _run_by_idempotency(key: str, request: Request) -> RunRow | None:
    from langhost.core_api import _authorized_run

    thread_id = UUID(str(request.path_params["thread_id"]))
    scoped_key = scoped_idempotency_key(principal_from_scope(request.scope), key)
    if scoped_key is None:
        return None
    async with connect() as conn:
        query = select(RunRow).where(
            RunRow.idempotency_key == scoped_key, RunRow.thread_id == thread_id
        )
        row = (await conn.session.execute(query)).scalar_one_or_none()
        return (
            await _authorized_run(request, conn.session, row.run_id, "update")
            if row is not None
            else None
        )


async def protocol_commands(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return _error(None, "invalid_argument", "command body must be JSON", 400)
    if not isinstance(body, dict):
        return _error(None, "invalid_argument", "command body must be an object", 400)
    command_id = _command_id(body)
    if not isinstance(command_id, int) or isinstance(command_id, bool):
        return _error(
            command_id if command_id is not None else None,
            "invalid_argument",
            "id must be an integer",
            400,
        )
    method = body.get("method")
    if not isinstance(method, str) or not method:
        return _error(command_id, "invalid_argument", "method must be a non-empty string", 400)
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return _error(command_id, "invalid_argument", "params must be an object", 400)
    try:
        thread_id = UUID(str(request.path_params["thread_id"]))
    except (KeyError, TypeError, ValueError):
        return _error(command_id, "invalid_argument", "thread_id must be a UUID", 400)

    from langhost.core_api import runs_create

    if method == "run.start":
        assistant_id = params.get("assistant_id")
        if not assistant_id:
            return _error(command_id, "invalid_argument", "assistant_id is required")
        payload = {
            "assistant_id": assistant_id,
            "input": params.get("input"),
            "config": params.get("config") or {},
            "metadata": params.get("metadata") or {},
            "if_not_exists": "create",
        }
        if payload["input"] is None:
            payload.pop("input")
        result = await runs_create(request, thread_value=str(thread_id), payload=payload)
        if result.status_code >= 300:
            detail = json.loads(bytes(result.body)).get("detail", "run start failed")
            return _error(command_id, "invalid_argument", str(detail))
        run = json.loads(bytes(result.body))
        return JSONResponse(
            {
                "id": command_id,
                "type": "success",
                "result": {"run_id": run["run_id"], "thread_id": str(thread_id)},
                "meta": {"applied_through_seq": 0},
            }
        )

    if method == "input.respond":
        interrupt_id = str(params.get("interrupt_id") or "")
        if not interrupt_id:
            return _error(command_id, "invalid_argument", "interrupt_id is required")
        resume_key = f"protocol-resume:{thread_id}:{interrupt_id}"
        existing = await _run_by_idempotency(resume_key, request)
        if existing is not None and existing.thread_id == thread_id:
            return JSONResponse(
                {
                    "id": command_id,
                    "type": "success",
                    "result": {"run_id": str(existing.run_id), "thread_id": str(thread_id)},
                    "meta": {"applied_through_seq": 0},
                }
            )
        thread = await _thread(request, thread_id, "update")
        interrupt = thread.interrupts.get(interrupt_id) if thread is not None else None
        if interrupt is None:
            return _error(command_id, "no_such_interrupt", "interrupt does not exist")
        assert thread is not None
        latest = await _latest_run(thread_id, request)
        if latest is None:
            return _error(command_id, "no_such_run", "thread has no run")
        command = {"resume": {interrupt_id: params.get("response")}}
        for field in ("graph", "update", "goto"):
            if field in params:
                command[field] = params[field]
        payload = {
            "assistant_id": str(latest.assistant_id),
            "command": command,
            "metadata": {},
            "idempotency_key": resume_key,
            "if_not_exists": "reject",
            "version": latest.kwargs.get("version", "v2"),
            "stream_resumable": bool(latest.kwargs.get("stream_resumable", False)),
        }
        result = await runs_create(request, thread_value=str(thread_id), payload=payload)
        if result.status_code >= 300:
            detail = json.loads(bytes(result.body)).get("detail", "resume failed")
            return _error(command_id, "invalid_argument", str(detail))
        run = json.loads(bytes(result.body))
        # A resume consumes exactly one persisted interrupt. Keep other
        # outstanding interrupts intact so the official SDK can address them
        # independently; the idempotency lookup above handles duplicate resumes.
        async with connect() as conn:
            persisted = await conn.session.get(ThreadRow, thread_id)
            if persisted is not None and await _thread(request, thread_id, "update") is not None:
                persisted.interrupts = {
                    key: value for key, value in persisted.interrupts.items() if key != interrupt_id
                }
                persisted.status = "busy"
        return JSONResponse(
            {
                "id": command_id,
                "type": "success",
                "result": {"run_id": run["run_id"], "thread_id": str(thread_id)},
                "meta": {"applied_through_seq": thread.event_seq},
            }
        )

    return _error(command_id, "unknown_command", f"unsupported protocol method: {method!r}")


def _namespace_matches(namespace: list[str], selectors: Any, depth: Any) -> bool:
    if not selectors:
        return True
    if not isinstance(selectors, list):
        return False
    try:
        depth_value = int(depth) if depth is not None else None
    except (TypeError, ValueError):
        depth_value = None
    for selector in selectors:
        if not isinstance(selector, list):
            continue
        selected = [str(item) for item in selector]
        if namespace[: len(selected)] != selected:
            continue
        distance = len(namespace) - len(selected)
        if depth_value is None or distance <= max(depth_value, 0):
            return True
    return False


def _channel_matches(method: str, channels: Any) -> bool:
    if not channels or "*" in channels:
        return True
    if not isinstance(channels, list):
        return False
    channel = "input" if method.startswith("input.") else method.split(":", 1)[0]
    return channel in channels or method in channels


def _wire_from_row(row: RuntimeEventRow) -> dict[str, Any]:
    return protocol_event(
        event_id=str(row.event_id),
        sequence=row.sequence,
        run_id=str(row.run_id) if row.run_id else "",
        thread_id=str(row.thread_id) if row.thread_id else "",
        event=row.payload,
    )


PROTOCOL_REPLAY_PAGE_SIZE = 32


async def _load_protocol_events(thread_id: UUID, since: int) -> tuple[int, list[dict[str, Any]]]:
    async with connect() as conn:
        thread = await conn.session.scalar(
            select(ThreadRow).where(ThreadRow.thread_id == thread_id).with_for_update(read=True)
        )
        if thread is None:
            return 0, []
        query = (
            select(RuntimeEventRow)
            .where(RuntimeEventRow.thread_id == thread_id, RuntimeEventRow.sequence > since)
            .order_by(RuntimeEventRow.sequence)
            .limit(PROTOCOL_REPLAY_PAGE_SIZE)
        )
        rows = (await conn.session.execute(query)).scalars().all()
    return thread.event_pruned_through, [_wire_from_row(row) for row in rows]


def _wire_matches(wire: dict[str, Any], body: dict[str, Any]) -> bool:
    if not isinstance(wire, dict):
        return False
    method = str(wire.get("method") or "")
    params = wire.get("params")
    params_dict = params if isinstance(params, dict) else {}
    ns = params_dict.get("namespace")
    ns_list = list(ns) if isinstance(ns, (list, tuple)) else []
    return _channel_matches(method, body.get("channels")) and _namespace_matches(
        ns_list, body.get("namespaces"), body.get("depth")
    )


def _frame(wire: dict[str, Any]) -> str:
    seq = wire.get("seq")
    data = json.dumps(wire, ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: event\ndata: {data}\n\n"


def _safe_uuid(value: Any) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _wire_run_id(wire: dict[str, Any]) -> UUID | None:
    params = wire.get("params")
    return _safe_uuid(params.get("run_id")) if isinstance(params, dict) else None


async def protocol_event_stream(request: Request) -> JSONResponse | StreamingResponse:
    from langhost.streaming import _resumable_run_ids

    try:
        thread_id = UUID(str(request.path_params["thread_id"]))
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"detail": "thread not found"}, status_code=404)
    thread = await _thread(request, thread_id)
    if thread is None:
        return JSONResponse({"detail": "thread not found"}, status_code=404)
    try:
        body = await request.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        return JSONResponse({"detail": "event stream body must be JSON"}, status_code=422)
    if not isinstance(body, dict):
        return JSONResponse({"detail": "event stream body must be an object"}, status_code=422)
    channels = body.get("channels")
    if not isinstance(channels, list) or not channels:
        return JSONResponse({"detail": "channels must be a non-empty array"}, status_code=422)
    try:
        since = max(int(body.get("since", 0) or 0), 0)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "since must be an integer"}, status_code=422)
    heartbeat = max(float(os.environ.get("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "15")), 0.1)
    timeout = max(float(os.environ.get("GRAPHHARBOR_PROTOCOL_TIMEOUT_SECONDS", "3600")), heartbeat)
    manager = get_stream_manager()
    queue = await manager.add_thread_stream(thread_id)
    try:
        watermark, replay = await _load_protocol_events(thread_id, since)
        if since and since < watermark:
            await manager.remove_thread_stream(thread_id, queue)
            return JSONResponse(
                {
                    "code": "cursor_expired",
                    "detail": "cursor_expired",
                    "recovery": "thread_snapshot",
                },
                status_code=410,
            )
        active_interrupt_ids = (
            set(thread.interrupts.keys()) if isinstance(thread.interrupts, dict) else set()
        )

        def _is_active_protocol_event(w: Any) -> bool:
            if not isinstance(w, dict):
                return True
            method = str(w.get("method") or "")
            params = w.get("params")
            params_dict = params if isinstance(params, dict) else {}
            data = params_dict.get("data")
            data_dict = data if isinstance(data, dict) else {}
            data_event = str(data_dict.get("event") or "")

            is_input_req = (
                method == "input.requested"
                or (method == "input" and data_event == "requested")
                or data_event == "input.requested"
            )
            if is_input_req:
                iid = str(data_dict.get("interrupt_id") or params_dict.get("interrupt_id") or "")
                if iid and iid not in active_interrupt_ids:
                    return False
            return True

    except Exception:
        await manager.remove_thread_stream(thread_id, queue)
        raise

    async def stream() -> AsyncIterator[str]:
        metric_inc("graphharbor_protocol_connections_opened_total")
        if since:
            metric_inc("graphharbor_protocol_replays_total")
        seen: set[int] = set()
        loop = asyncio.get_running_loop()
        last_sent_at = loop.time()
        last_auth_at = loop.time()

        async def _check_authorized() -> bool:
            nonlocal last_auth_at
            now_ = loop.time()
            if now_ - last_auth_at < 10.0:
                return True
            if await _thread(request, thread_id) is None:
                return False
            last_auth_at = now_
            return True

        try:
            if not await _check_authorized():
                return
            # Transfer and release each bounded page before waiting on live SSE.
            page = replay[:]
            replay.clear()
            cursor = since
            while page:
                resumable = await _resumable_run_ids(
                    {uid for wire in page if (uid := _wire_run_id(wire)) is not None}
                )
                page_count = len(page)
                for wire in page:
                    seq = wire.get("seq")
                    if not isinstance(seq, int):
                        continue
                    cursor = max(cursor, seq)
                    uid = _wire_run_id(wire)
                    if (
                        (uid is None or uid in resumable)
                        and _is_active_protocol_event(wire)
                        and _wire_matches(wire, body)
                    ):
                        seen.add(seq)
                        metric_inc("graphharbor_protocol_events_total")
                        yield _frame(wire)
                        last_sent_at = loop.time()
                page.clear()
                if page_count < PROTOCOL_REPLAY_PAGE_SIZE:
                    break
                if await request.is_disconnected() or not await _check_authorized():
                    return
                watermark, page = await _load_protocol_events(thread_id, cursor)
                if cursor and cursor < watermark:
                    return  # Reconnect receives 410 and recovers from a snapshot.
            started = loop.time()
            while loop.time() - started < timeout:
                now = loop.time()
                remaining = max(0.1, heartbeat - (now - last_sent_at))
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    if not await _check_authorized():
                        return
                    yield ": heartbeat\n\n"
                    last_sent_at = loop.time()
                    continue
                if not await _check_authorized():
                    return
                try:
                    payload = json.loads(message.data)
                except (TypeError, ValueError, json.JSONDecodeError):
                    payload = None
                if not isinstance(payload, dict):
                    if loop.time() - last_sent_at >= heartbeat:
                        yield ": heartbeat\n\n"
                        last_sent_at = loop.time()
                    continue
                wire = payload
                seq = wire.get("seq")
                if (
                    not isinstance(seq, int)
                    or seq <= since
                    or seq in seen
                    or not _wire_matches(wire, body)
                ):
                    if loop.time() - last_sent_at >= heartbeat:
                        yield ": heartbeat\n\n"
                        last_sent_at = loop.time()
                    continue
                seen.add(seq)
                metric_inc("graphharbor_protocol_events_total")
                yield _frame(wire)
                last_sent_at = loop.time()
            yield ": stream timeout\n\n"
        finally:
            metric_inc("graphharbor_protocol_connections_closed_total")
            await manager.remove_thread_stream(thread_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["protocol_commands", "protocol_event_stream"]
