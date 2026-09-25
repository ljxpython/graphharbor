from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph_sdk import Auth
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from starlette.exceptions import HTTPException
from starlette.requests import Request


def test_v2_failure_event_is_emitted_independent_of_stream_mode() -> None:
    from langhost.streaming import _event_frame

    frame = _event_frame(
        {
            "seq": 7,
            "event": {
                "event": "lifecycle",
                "status": "error",
                "error": {"type": "ValueError", "message": "fixture failure"},
            },
        },
        modes={"values"},
        stream_subgraphs=False,
    )
    assert frame == ("error", {"error": "ValueError", "message": "fixture failure"}, 7)


@pytest.mark.asyncio
@pytest.mark.parametrize("batches,last_batch", [(2, 500), (20, 1000)])
async def test_reaper_drains_bounded_event_batches_per_tick(
    monkeypatch, batches: int, last_batch: int
) -> None:
    from langgraph_runtime_pg.metrics import get_metrics, reset_metrics
    from langgraph_runtime_pg.production_worker import ProductionWorker

    worker = ProductionWorker.__new__(ProductionWorker)
    worker.stop_event = asyncio.Event()
    worker.reap_once = AsyncMock(return_value=0)
    calls = 0

    class Repository:
        async def prune_expired_events(self, _session, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == batches:
                worker.stop_event.set()
                return last_batch
            return 1000

    @asynccontextmanager
    async def fake_connect():
        yield SimpleNamespace(session=object())

    worker.repository = Repository()
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.connect", fake_connect)
    monkeypatch.setenv("GRAPHHARBOR_REAPER_INTERVAL_SECONDS", "0.5")
    reset_metrics()
    await worker._reaper_loop()
    assert calls == batches
    assert get_metrics()["graphharbor_runtime_events_pruned_total"][""] == (
        (batches - 1) * 1000 + last_batch
    )
    assert "graphharbor_runtime_event_prune_duration_ms" in get_metrics()


@pytest.mark.asyncio
async def test_redis_fanout_failure_preserves_postgres_replay(monkeypatch) -> None:
    assert "graphharbor_event_retention_verify" in os.environ.get("DATABASE_URI", "")

    from langgraph_runtime_pg.database import connect, start_pool, stop_pool
    from langgraph_runtime_pg.models import AssistantRow, RunRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker

    assistant_id, thread_id, run_id = uuid4(), uuid4(), uuid4()

    async def unavailable(*_args, **_kwargs):
        raise ConnectionError("injected Redis outage")

    monkeypatch.setattr(
        "langgraph_runtime_pg.production_worker.get_stream_manager",
        lambda: SimpleNamespace(put=unavailable),
    )
    await start_pool()
    try:
        async with connect() as conn:
            conn.session.add(AssistantRow(assistant_id=assistant_id, graph_id="retention-test"))
            conn.session.add(ThreadRow(thread_id=thread_id, metadata_={}, config={}, interrupts={}))
            conn.session.add(
                RunRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    assistant_id=assistant_id,
                    status="running",
                    kwargs={},
                    metadata_={},
                )
            )
        worker = ProductionWorker(SimpleNamespace(), owner="retention-test")
        await worker._publish_event(
            run_id, thread_id, {"event": "messages", "data": {"content": "retained"}}
        )
        async with connect() as conn:
            rows = list(
                (
                    await conn.session.scalars(
                        select(RuntimeEventRow).where(RuntimeEventRow.run_id == run_id)
                    )
                ).all()
            )
        assert len(rows) == 1
        assert rows[0].payload["data"]["content"] == "retained"
    finally:
        await stop_pool()


@pytest.mark.asyncio
async def test_redis_heartbeat_outage_does_not_cancel_postgres_owned_run(monkeypatch) -> None:
    from langgraph_runtime_pg.production_worker import ProductionWorker

    renewed, checked = asyncio.Event(), asyncio.Event()

    class Repository:
        lease_seconds = 5

        async def renew(self, _session, _run_id, _owner):
            renewed.set()
            return True

    @asynccontextmanager
    async def fake_connect():
        yield SimpleNamespace(session=object())

    async def unavailable(_run_id):
        raise RedisConnectionError("injected Redis outage")

    async def not_cancelled(_run_id, _thread_id):
        checked.set()
        return False

    worker = ProductionWorker.__new__(ProductionWorker)
    worker.repository = Repository()
    worker.owner = "retention-test"
    worker.stop_event = asyncio.Event()
    worker._cancel_requested = not_cancelled
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.connect", fake_connect)
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.set_run_heartbeat", unavailable)
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.bg_job_heartbeat_secs", lambda: 1)

    cancel_event = asyncio.Event()
    task = asyncio.create_task(worker._heartbeat(uuid4(), None, cancel_event))
    try:
        await asyncio.wait_for(checked.wait(), timeout=2)
        assert renewed.is_set()
        assert not cancel_event.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["success", "error"])
async def test_expired_event_pruning_is_bounded_and_preserves_active_state(
    monkeypatch, terminal_status: str
) -> None:
    assert "graphharbor_event_retention_verify" in os.environ.get("DATABASE_URI", "")

    from langgraph_runtime_pg.auth import Principal
    from langgraph_runtime_pg.database import connect, start_pool, stop_pool
    from langgraph_runtime_pg.models import (
        AssistantRow,
        RunCheckpointBaselineRow,
        RunRow,
        RuntimeEventRow,
        StoreItemRow,
        ThreadRow,
    )
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.protocol_api import _load_protocol_events, protocol_event_stream
    from langhost.streaming import _run_sse, _thread_events, thread_stream

    class FakeManager:
        async def add_queue(self, *_args, **_kwargs):
            return asyncio.Queue()

        async def remove_queue(self, *_args):
            return None

        async def add_thread_stream(self, *_args):
            return asyncio.Queue()

        async def remove_thread_stream(self, *_args):
            return None

    manager = FakeManager()
    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: manager)
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)

    auth = Auth()

    @auth.on.threads.read
    async def deny_read(ctx, value):
        del ctx, value
        return False

    def request(
        path: str,
        thread_id,
        *,
        cursor: str | None = None,
        body: dict | None = None,
        denied: bool = False,
        auth_handler: Auth | None = None,
        identity: str = "denied-user",
    ):
        payload = json.dumps(body or {}).encode()

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        scope = {
            "type": "http",
            "method": "POST" if body is not None else "GET",
            "path": path,
            "headers": [(b"last-event-id", cursor.encode())] if cursor else [],
            "query_string": b"",
            "path_params": {"thread_id": str(thread_id)},
            "app": SimpleNamespace(
                state=SimpleNamespace(auth_handler=auth_handler or (auth if denied else None))
            ),
        }
        if denied or auth_handler is not None:
            scope["principal"] = Principal.from_auth_user({"identity": identity})
        return Request(scope, receive=receive)

    await start_pool()
    try:
        now = datetime.now(UTC)
        old = now - timedelta(days=2)
        assistant_id = uuid4()
        old_thread, active_thread, fresh_thread = uuid4(), uuid4(), uuid4()
        old_run, active_run, fresh_run = uuid4(), uuid4(), uuid4()
        store_key = uuid4().hex
        async with connect() as conn:
            conn.session.add(AssistantRow(assistant_id=assistant_id, graph_id="retention-test"))
            conn.session.add_all(
                [
                    ThreadRow(
                        thread_id=thread_id,
                        event_seq=6 if thread_id == old_thread else 1,
                        metadata_={"owner": "alice"} if thread_id == old_thread else {},
                        config={},
                        interrupts={},
                    )
                    for thread_id in (old_thread, active_thread, fresh_thread)
                ]
            )
            conn.session.add_all(
                [
                    RunRow(
                        run_id=run_id,
                        thread_id=thread_id,
                        assistant_id=assistant_id,
                        status=status,
                        updated_at=updated_at,
                        event_seq=6 if run_id == old_run else 1,
                        kwargs={},
                        metadata_={},
                    )
                    for run_id, thread_id, status, updated_at in (
                        (old_run, old_thread, terminal_status, old),
                        (active_run, active_thread, "running", old),
                        (fresh_run, fresh_thread, "success", now),
                    )
                ]
            )
            await conn.session.flush()
            conn.session.add(
                RunCheckpointBaselineRow(
                    run_id=old_run,
                    thread_id=old_thread,
                    checkpoints=[{"checkpoint_id": "retention-baseline"}],
                    writes=[],
                    projection={"values": {"kept": True}},
                )
            )
            conn.session.add(
                StoreItemRow(
                    prefix="retention-test",
                    key=store_key,
                    value={"preserved": True},
                )
            )
            conn.session.add_all(
                [
                    RuntimeEventRow(
                        run_id=old_run,
                        thread_id=old_thread,
                        sequence=sequence,
                        topic="messages" if sequence < 6 else "lifecycle",
                        payload=(
                            {
                                "event": "lifecycle",
                                "status": terminal_status,
                                "error": {"message": "fixture failure"},
                            }
                            if sequence == 6 and terminal_status == "error"
                            else {"event": "messages" if sequence < 6 else "lifecycle"}
                        ),
                        terminal=sequence == 6,
                        created_at=old,
                    )
                    for sequence in range(1, 7)
                ]
                + [
                    RuntimeEventRow(
                        run_id=run_id,
                        thread_id=thread_id,
                        sequence=1,
                        topic="messages",
                        payload={"event": "messages"},
                        created_at=old,
                    )
                    for run_id, thread_id in (
                        (active_run, active_thread),
                        (fresh_run, fresh_thread),
                    )
                ]
                + [
                    RuntimeEventRow(
                        thread_id=old_thread,
                        sequence=7,
                        topic="custom",
                        payload={"event": "custom"},
                        created_at=old,
                    )
                ]
            )

        async def prune() -> int:
            async with connect() as conn:
                return await RunRepository().prune_expired_events(
                    conn.session, retention_seconds=86400, batch_size=2, now=now
                )

        assert sum(await asyncio.gather(prune(), prune())) in (2, 4)
        async with connect() as conn:
            remaining_raw = await conn.session.scalar(
                select(func.count())
                .select_from(RuntimeEventRow)
                .where(
                    RuntimeEventRow.run_id == old_run,
                    RuntimeEventRow.terminal.is_(False),
                )
            )
        assert remaining_raw in (1, 3)
        if remaining_raw == 3:
            assert await prune() == 2
        assert await prune() == 1
        assert await prune() == 0
        async with connect() as conn:
            old_row = await conn.session.get(RunRow, old_run)
            thread_row = await conn.session.get(ThreadRow, old_thread)
            checkpoint_baseline = await conn.session.get(RunCheckpointBaselineRow, old_run)
            store_item = await conn.session.get(StoreItemRow, ("retention-test", store_key))
            remaining = (
                await conn.session.scalars(
                    select(RuntimeEventRow)
                    .where(RuntimeEventRow.run_id == old_run)
                    .order_by(RuntimeEventRow.sequence)
                )
            ).all()
            other_count = await conn.session.scalar(
                select(func.count())
                .select_from(RuntimeEventRow)
                .where(
                    RuntimeEventRow.run_id.in_((active_run, fresh_run))
                    | (RuntimeEventRow.run_id.is_(None) & (RuntimeEventRow.thread_id == old_thread))
                )
            )
            assert old_row is not None and old_row.event_pruned_through == 5
            assert thread_row is not None and thread_row.event_pruned_through == 5
            assert checkpoint_baseline is not None
            assert checkpoint_baseline.projection == {"values": {"kept": True}}
            assert store_item is not None and store_item.value == {"preserved": True}
            assert [row.sequence for row in remaining] == [6]
            if terminal_status == "error":
                assert remaining[0].payload["error"]["message"] == "fixture failure"
            assert other_count == 3
        thread_watermark, thread_rows = await _thread_events(old_thread, 0)
        protocol_watermark, protocol_rows = await _load_protocol_events(old_thread, 0)
        assert thread_watermark == protocol_watermark == 5
        assert [row.sequence for row in thread_rows] == [6, 7]
        assert [row["seq"] for row in protocol_rows] == [6, 7]
        non_resumable = await _run_sse(
            request(f"/threads/{old_thread}/runs/{old_run}/stream", old_thread),
            run_id=old_run,
            thread_id=old_thread,
            payload={},
            include_location=False,
        )
        with pytest.raises(StopAsyncIteration):
            await anext(non_resumable.body_iterator)
        monkeypatch.setenv("GRAPHHARBOR_THREAD_STREAM_HEARTBEAT_SECONDS", "0.1")
        thread_non_resumable = await thread_stream(
            request(f"/threads/{old_thread}/stream", old_thread, cursor="-")
        )
        assert "event: custom" in await anext(thread_non_resumable.body_iterator)
        assert "heartbeat" in await anext(thread_non_resumable.body_iterator)
        await thread_non_resumable.body_iterator.aclose()
        monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "0.1")
        protocol_non_resumable = await protocol_event_stream(
            request(
                f"/threads/{old_thread}/stream/events",
                old_thread,
                body={"channels": ["lifecycle"], "since": 0},
            )
        )
        assert "heartbeat" in await anext(protocol_non_resumable.body_iterator)
        await protocol_non_resumable.body_iterator.aclose()
        run_response = await _run_sse(
            request(f"/threads/{old_thread}/runs/{old_run}/stream", old_thread, cursor="1"),
            run_id=old_run,
            thread_id=old_thread,
            payload={},
            include_location=False,
        )
        assert "cursor_expired" in await anext(run_response.body_iterator)
        await run_response.body_iterator.aclose()
        thread_response = await thread_stream(
            request(f"/threads/{old_thread}/stream", old_thread, cursor="1-0")
        )
        assert "cursor_expired" in await anext(thread_response.body_iterator)
        await thread_response.body_iterator.aclose()
        protocol_response = await protocol_event_stream(
            request(
                f"/threads/{old_thread}/stream/events",
                old_thread,
                body={"channels": ["lifecycle"], "since": 1},
            )
        )
        assert protocol_response.status_code == 410
        assert "cursor_expired" in protocol_response.body.decode()
        for attempt in (
            _run_sse(
                request(
                    f"/threads/{old_thread}/runs/{old_run}/stream",
                    old_thread,
                    cursor="1",
                    denied=True,
                ),
                run_id=old_run,
                thread_id=old_thread,
                payload={},
                include_location=False,
            ),
            thread_stream(
                request(f"/threads/{old_thread}/stream", old_thread, cursor="1-0", denied=True)
            ),
            protocol_event_stream(
                request(
                    f"/threads/{old_thread}/stream/events",
                    old_thread,
                    body={"channels": ["lifecycle"], "since": 1},
                    denied=True,
                )
            ),
        ):
            with pytest.raises(HTTPException) as denied_error:
                await attempt
            assert denied_error.value.status_code == 403

        scoped_auth = Auth()
        access = {"alice": True, "bob": True}

        @scoped_auth.on.threads.read
        async def owner_read(ctx, value):
            del value
            return {"owner": ctx.user.identity} if access.get(ctx.user.identity) else False

        def scoped_request(path, *, identity="alice", cursor=None, body=None):
            return request(
                path,
                old_thread,
                cursor=cursor,
                body=body,
                auth_handler=scoped_auth,
                identity=identity,
            )

        run_path = f"/threads/{old_thread}/runs/{old_run}/stream"
        thread_path = f"/threads/{old_thread}/stream"
        protocol_path = f"/threads/{old_thread}/stream/events"
        protocol_body = {"channels": ["lifecycle"], "since": 1}

        allowed_run = await _run_sse(
            scoped_request(run_path, cursor="1"),
            run_id=old_run,
            thread_id=old_thread,
            payload={},
            include_location=False,
        )
        assert "cursor_expired" in await anext(allowed_run.body_iterator)
        await allowed_run.body_iterator.aclose()
        allowed_thread = await thread_stream(scoped_request(thread_path, cursor="1-0"))
        assert "cursor_expired" in await anext(allowed_thread.body_iterator)
        await allowed_thread.body_iterator.aclose()
        allowed_protocol = await protocol_event_stream(
            scoped_request(protocol_path, body=protocol_body)
        )
        assert allowed_protocol.status_code == 410

        access["alice"] = False
        for attempt in (
            _run_sse(
                scoped_request(run_path, cursor="1"),
                run_id=old_run,
                thread_id=old_thread,
                payload={},
                include_location=False,
            ),
            thread_stream(scoped_request(thread_path, cursor="1-0")),
            protocol_event_stream(scoped_request(protocol_path, body=protocol_body)),
        ):
            with pytest.raises(HTTPException) as denied_error:
                await attempt
            assert denied_error.value.status_code == 403

        with pytest.raises(HTTPException) as cross_user_error:
            await _run_sse(
                scoped_request(run_path, identity="bob", cursor="1"),
                run_id=old_run,
                thread_id=old_thread,
                payload={},
                include_location=False,
            )
        assert cross_user_error.value.status_code == 404
        cross_user_thread = await thread_stream(
            scoped_request(thread_path, identity="bob", cursor="1-0")
        )
        assert "404" in await anext(cross_user_thread.body_iterator)
        await cross_user_thread.body_iterator.aclose()
        cross_user_protocol = await protocol_event_stream(
            scoped_request(protocol_path, identity="bob", body=protocol_body)
        )
        assert cross_user_protocol.status_code == 404
    finally:
        await stop_pool()
