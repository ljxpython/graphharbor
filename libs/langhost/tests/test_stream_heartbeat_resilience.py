"""Tests for deterministic stream heartbeats and resilience under filtered traffic."""

from __future__ import annotations

import asyncio
import contextlib
import json
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request
from starlette.responses import StreamingResponse

from langgraph_runtime_pg.models import ThreadRow
from langgraph_runtime_pg.protocol import protocol_event
from langgraph_runtime_pg.redis_stream import Message
from langhost.protocol_api import protocol_event_stream
from langhost.streaming import thread_stream


class InMemoryStreamManager:
    def __init__(self) -> None:
        self.queues: dict[UUID, list[asyncio.Queue]] = {}
        self.removed_count = 0

    async def add_thread_stream(self, thread_id: UUID) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self.queues.setdefault(thread_id, []).append(q)
        return q

    async def remove_thread_stream(self, thread_id: UUID, queue: asyncio.Queue) -> None:
        if thread_id in self.queues:
            self.queues[thread_id] = [q for q in self.queues[thread_id] if q is not queue]
            self.removed_count += 1

    async def publish_thread_event(self, thread_id: UUID, message: Message) -> None:
        for q in self.queues.get(thread_id, []):
            await q.put(message)


@pytest.mark.asyncio
async def test_protocol_replay_reads_all_pages_and_releases_them(monkeypatch):
    from langhost.protocol_api import PROTOCOL_REPLAY_PAGE_SIZE

    thread_id = uuid4()
    manager = InMemoryStreamManager()
    loaded = []
    pages = []
    total = PROTOCOL_REPLAY_PAGE_SIZE * 2 + 1

    async def load(_thread, since):
        loaded.append(since)
        page = [
            {"seq": seq, "method": "custom", "params": {"data": "test"}}
            for seq in range(since + 1, min(since + PROTOCOL_REPLAY_PAGE_SIZE, total) + 1)
        ]
        pages.append(page)
        return 0, page

    monkeypatch.setattr("langhost.protocol_api._load_protocol_events", load)
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.protocol_api._thread",
        lambda *a: asyncio.sleep(0, result=ThreadRow(thread_id=thread_id, interrupts={})),
    )
    monkeypatch.setattr(
        "langhost.streaming._resumable_run_ids", lambda ids: asyncio.sleep(0, result=ids)
    )
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "0.1")
    scope, receive = _make_scope_and_receive(
        "POST", "/stream", {"thread_id": str(thread_id)}, {"channels": ["custom"]}
    )
    response = await protocol_event_stream(Request(scope, receive))
    frames = []
    try:
        async for frame in response.body_iterator:
            if frame.startswith(": heartbeat"):
                break
            frames.append(frame)
        assert len(frames) == total
        assert loaded == [0, PROTOCOL_REPLAY_PAGE_SIZE, PROTOCOL_REPLAY_PAGE_SIZE * 2]
        assert all(not page for page in pages)
    finally:
        await response.body_iterator.aclose()
    assert manager.removed_count == 1


def _make_scope_and_receive(
    method: str,
    path: str,
    path_params: dict[str, str],
    body_dict: dict | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[dict, callable]:
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin1"), v.encode("latin1")))
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": raw_headers,
        "path_params": path_params,
        "query_string": b"",
    }
    body_bytes = json.dumps(body_dict).encode("utf-8") if body_dict is not None else b""

    async def receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    return scope, receive


@pytest.mark.asyncio
async def test_protocol_event_stream_heartbeat_under_filtered_traffic(monkeypatch):
    """Ensure protocol stream emits heartbeats even when inundated with filtered events."""
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "0.15")
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_TIMEOUT_SECONDS", "2.0")

    thread_id = uuid4()
    mock_thread = ThreadRow(
        thread_id=thread_id,
        status="idle",
        metadata_={},
        config={},
        values_={},
        interrupts={},
        error=None,
    )

    manager = InMemoryStreamManager()

    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.protocol_api._thread",
        lambda *a, **k: asyncio.sleep(0, result=mock_thread),
    )
    monkeypatch.setattr(
        "langhost.protocol_api._load_protocol_events",
        lambda *a, **k: asyncio.sleep(0, result=(0, [])),
    )

    scope, receive = _make_scope_and_receive(
        "POST",
        f"/threads/{thread_id}/stream/events",
        {"thread_id": str(thread_id)},
        body_dict={"channels": ["input"], "since": 0},
    )
    req = Request(scope, receive)
    res = await protocol_event_stream(req)
    assert isinstance(res, StreamingResponse)

    async def pump_filtered_events():
        for seq in range(1, 20):
            await asyncio.sleep(0.03)
            wire = protocol_event(
                event_id=f"evt-{seq}",
                sequence=seq,
                run_id=str(uuid4()),
                thread_id=str(thread_id),
                event={"event": "filtered_internal", "data": "ignore_me"},
            )
            wire["method"] = "debug:internal"
            await manager.publish_thread_event(
                thread_id,
                Message(
                    topic=b"thread",
                    id=f"{seq}-0".encode("ascii"),
                    data=json.dumps(wire).encode("utf-8"),
                ),
            )

    pump_task = asyncio.create_task(pump_filtered_events())
    heartbeats_received = 0
    try:
        async for chunk in res.body_iterator:
            if chunk.startswith(": heartbeat"):
                heartbeats_received += 1
                if heartbeats_received >= 2:
                    break
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        await res.body_iterator.aclose()

    assert heartbeats_received >= 2, f"Expected >= 2 heartbeats, got {heartbeats_received}"
    assert manager.removed_count >= 1, "Queue must be cleaned up in finally block"


@pytest.mark.asyncio
async def test_thread_stream_heartbeat_under_empty_queue(monkeypatch):
    """Ensure thread_stream emits heartbeat when idle."""
    monkeypatch.setenv("GRAPHHARBOR_THREAD_STREAM_HEARTBEAT_SECONDS", "0.15")

    thread_id = uuid4()
    mock_thread = ThreadRow(
        thread_id=thread_id,
        status="idle",
        metadata_={},
        config={},
        values_={},
        interrupts={},
        error=None,
    )

    manager = InMemoryStreamManager()

    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.core_api._get_thread",
        lambda *a, **k: asyncio.sleep(0, result=(mock_thread, None, thread_id)),
    )
    monkeypatch.setattr(
        "langhost.streaming._thread_event_sequence",
        lambda *a, **k: asyncio.sleep(0, result=0),
    )
    monkeypatch.setattr(
        "langhost.streaming._thread_events",
        lambda *a, **k: asyncio.sleep(0, result=(0, [])),
    )

    scope, receive = _make_scope_and_receive(
        "GET",
        f"/threads/{thread_id}/stream",
        {"thread_id": str(thread_id)},
    )
    req = Request(scope, receive)
    res = await thread_stream(req)
    assert isinstance(res, StreamingResponse)

    heartbeats_received = 0
    try:
        async for chunk in res.body_iterator:
            if chunk.startswith(": heartbeat"):
                heartbeats_received += 1
                if heartbeats_received >= 2:
                    break
    finally:
        await res.body_iterator.aclose()

    assert heartbeats_received >= 2
    assert manager.removed_count >= 1


@pytest.mark.asyncio
async def test_stream_response_headers_compliance(monkeypatch):
    """Verify that SSE stream endpoints provide standardized headers."""
    thread_id = uuid4()
    mock_thread = ThreadRow(
        thread_id=thread_id,
        status="idle",
        metadata_={},
        config={},
        values_={},
        interrupts={},
        error=None,
    )

    manager = InMemoryStreamManager()
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.protocol_api._thread",
        lambda *a, **k: asyncio.sleep(0, result=mock_thread),
    )
    monkeypatch.setattr(
        "langhost.protocol_api._load_protocol_events",
        lambda *a, **k: asyncio.sleep(0, result=(0, [])),
    )
    monkeypatch.setattr(
        "langhost.core_api._get_thread",
        lambda *a, **k: asyncio.sleep(0, result=(mock_thread, None, thread_id)),
    )
    monkeypatch.setattr(
        "langhost.streaming._thread_event_sequence",
        lambda *a, **k: asyncio.sleep(0, result=0),
    )
    monkeypatch.setattr(
        "langhost.streaming._thread_events",
        lambda *a, **k: asyncio.sleep(0, result=(0, [])),
    )

    # 1. Test protocol stream
    scope, receive = _make_scope_and_receive(
        "POST",
        f"/threads/{thread_id}/stream/events",
        {"thread_id": str(thread_id)},
        body_dict={"channels": ["input"], "since": 0},
    )
    res_proto = await protocol_event_stream(Request(scope, receive))
    assert res_proto.status_code == 200
    assert res_proto.headers["connection"] == "keep-alive"
    assert "no-cache" in res_proto.headers["cache-control"]
    assert res_proto.headers["x-accel-buffering"] == "no"
    await res_proto.body_iterator.aclose()

    # 2. Test thread stream
    scope_t, receive_t = _make_scope_and_receive(
        "GET",
        f"/threads/{thread_id}/stream",
        {"thread_id": str(thread_id)},
    )
    res_thread = await thread_stream(Request(scope_t, receive_t))
    assert res_thread.status_code == 200
    assert res_thread.headers["connection"] == "keep-alive"
    assert "no-cache" in res_thread.headers["cache-control"]
    assert res_thread.headers["x-accel-buffering"] == "no"
    await res_thread.body_iterator.aclose()


@pytest.mark.asyncio
async def test_zombie_interrupt_replay_filtered(monkeypatch):
    """Ensure resolved/historical input.requested events are filtered from replay."""
    thread_id = uuid4()
    run_id = uuid4()

    # Active interrupts on thread only contains 'int-active'
    mock_thread = ThreadRow(
        thread_id=thread_id,
        status="interrupted",
        metadata_={},
        config={},
        values_={},
        interrupts={"int-active": {"id": "int-active", "value": "Approve step 2"}},
        error=None,
    )

    manager = InMemoryStreamManager()
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.protocol_api._thread",
        lambda *a, **k: asyncio.sleep(0, result=mock_thread),
    )
    monkeypatch.setattr(
        "langhost.streaming._resumable_run_ids",
        lambda run_ids: asyncio.sleep(0, result=set(run_ids)),
    )

    # 2 historical events: seq 1 is zombie (int-old), seq 2 is active (int-active)
    wire_old = protocol_event(
        event_id="evt-1",
        sequence=1,
        run_id=str(run_id),
        thread_id=str(thread_id),
        event={
            "event": "input.requested",
            "data": {"interrupt_id": "int-old", "value": "Old resolved request"},
        },
    )
    wire_active = protocol_event(
        event_id="evt-2",
        sequence=2,
        run_id=str(run_id),
        thread_id=str(thread_id),
        event={
            "event": "input.requested",
            "data": {"interrupt_id": "int-active", "value": "Active request"},
        },
    )

    monkeypatch.setattr(
        "langhost.protocol_api._load_protocol_events",
        lambda *a, **k: asyncio.sleep(0, result=(0, [wire_old, wire_active])),
    )

    scope, receive = _make_scope_and_receive(
        "POST",
        f"/threads/{thread_id}/stream/events",
        {"thread_id": str(thread_id)},
        body_dict={"channels": ["input"], "since": 0},
    )
    req = Request(scope, receive)
    res = await protocol_event_stream(req)
    assert isinstance(res, StreamingResponse)

    frames: list[str] = []
    try:
        async for chunk in res.body_iterator:
            frames.append(chunk)
            # Replay emits immediately, break after replay
            if "int-active" in chunk or len(frames) >= 2:
                break
    finally:
        await res.body_iterator.aclose()

    all_output = "".join(frames)
    assert "int-old" not in all_output, "Zombie interrupt 'int-old' must NOT be replayed to client!"
    assert "int-active" in all_output, "Active interrupt 'int-active' must be replayed to client!"


@pytest.mark.asyncio
async def test_protocol_stream_replay_with_non_dict_data_payloads(monkeypatch):
    """Ensure protocol stream replay handles events where data is a list, string, or params is malformed."""
    thread_id = uuid4()
    run_id = uuid4()

    mock_thread = ThreadRow(
        thread_id=thread_id,
        status="busy",
        metadata_={},
        config={},
        values_={},
        interrupts={},
        error=None,
    )

    manager = InMemoryStreamManager()
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setattr(
        "langhost.protocol_api._thread",
        lambda *a, **k: asyncio.sleep(0, result=mock_thread),
    )
    monkeypatch.setattr(
        "langhost.streaming._resumable_run_ids",
        lambda rids: asyncio.sleep(0, result=rids),
    )

    # Various weird payloads that occur in real production (list data, none params, string data)
    wire_list_data = {
        "type": "event",
        "event_id": "evt-list",
        "seq": 1,
        "method": "messages",
        "params": {
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "namespace": [],
            "data": [{"delta": {"type": "block-delta", "text": "hello"}}],
        },
    }
    wire_string_data = {
        "type": "event",
        "event_id": "evt-str",
        "seq": 2,
        "method": "custom",
        "params": {
            "run_id": str(run_id),
            "thread_id": str(thread_id),
            "namespace": [],
            "data": "plain string",
        },
    }
    wire_none_params = {
        "type": "event",
        "event_id": "evt-none-params",
        "seq": 3,
        "method": "custom",
        "params": None,
    }

    monkeypatch.setattr(
        "langhost.protocol_api._load_protocol_events",
        lambda *a, **k: asyncio.sleep(
            0, result=(0, [wire_list_data, wire_string_data, wire_none_params])
        ),
    )

    scope, receive = _make_scope_and_receive(
        "POST",
        f"/threads/{thread_id}/stream/events",
        {"thread_id": str(thread_id)},
        body_dict={"channels": ["messages", "custom"], "since": 0},
    )
    req = Request(scope, receive)
    res = await protocol_event_stream(req)
    assert isinstance(res, StreamingResponse)

    frames: list[str] = []
    try:
        async for chunk in res.body_iterator:
            frames.append(chunk)
            if len(frames) >= 2:
                break
    finally:
        await res.body_iterator.aclose()

    all_output = "".join(frames)
    assert "evt-list" in all_output
    assert "evt-str" in all_output
