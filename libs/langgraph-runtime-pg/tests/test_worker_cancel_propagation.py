from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
import redis.asyncio as redis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.models import RunLeaseRow, RunRow, RuntimeEventRow
from langgraph_runtime_pg.production_worker import ProductionWorker, _control_action
from langgraph_runtime_pg.redis_stream import Message, StreamManager, get_stream_manager
from langgraph_runtime_pg.run_store import RunOwnershipError, RunRepository, stopped_event
from langhost.server import create_app


async def new_run(client, *, threadless=False):
    assistant = (await client.post("/assistants", json={"graph_id": "cancel"})).json()
    thread_id = None if threadless else (await client.post("/threads", json={})).json()["thread_id"]
    path = "/runs" if threadless else f"/threads/{thread_id}/runs"
    run = (
        await client.post(path, json={"assistant_id": assistant["assistant_id"], "input": {}})
    ).json()
    return thread_id, run["run_id"]


def test_control_action_only_accepts_cancel_intents():
    for value in (b"interrupt", b'{"action":"interrupt"}', b"rollback", b'{"action":"rollback"}'):
        assert _control_action(Message(topic=b"control", data=value)) in {"interrupt", "rollback"}
    for value in (b"done", b"unknown", b'{"action":"done"}', b"[]", b"\xff"):
        assert _control_action(Message(topic=b"control", data=value)) is None


@pytest.mark.parametrize("stage", ["factory", "node", "stream", "subgraph"])
@pytest.mark.parametrize("threadless", [False, True])
async def test_cancel_wakes_before_first_heartbeat_and_waits_for_cleanup(
    pg_runtime, monkeypatch, stage, threadless
):
    monkeypatch.setenv("BG_JOB_HEARTBEAT_SECS", "60")
    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "60")
    entered, cleaning, release, exited = (asyncio.Event() for _ in range(4))

    async def block():
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            exited.set()

    @asynccontextmanager
    async def open_graph(*args):
        if stage == "factory":
            await block()
        yield object()

    async def invoke(*args, **kwargs):
        if stage == "subgraph":
            async with asyncio.TaskGroup() as group:
                group.create_task(block())
                await asyncio.Event().wait()
        else:
            await block()

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="cancel-owner")
    ticks = []
    original = worker.repository.renew

    async def renew(*args, **kwargs):
        ticks.append(time.monotonic())
        return await original(*args, **kwargs)

    monkeypatch.setattr(worker.repository, "renew", renew)
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client, threadless=threadless)
        task = asyncio.create_task(worker.run_once())
        waiter = None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            if threadless:
                response = await client.post("/runs/cancel", json={"run_ids": [run_id]})
                assert response.status_code == 204
            else:
                path = f"/threads/{thread_id}/runs/{run_id}/cancel"
                response = await client.post(path)
                assert response.status_code == 202 and response.content == b""
                waiter = asyncio.create_task(client.post(path + "?wait=true"))
            await asyncio.wait_for(cleaning.wait(), 1)
            assert ticks == [], "cancel depended on heartbeat"
            async with connect() as conn:
                row = await conn.session.get(RunRow, UUID(run_id))
                assert row.status == "interrupted" and row.lease_owner == worker.owner
                assert await stopped_event(conn.session, row) is None
                lease = await conn.session.get(RunLeaseRow, UUID(run_id))
                assert await worker.repository.renew(
                    conn.session, row.run_id, worker.owner, generation=lease.generation
                )
                assert await RunRepository().claim_next(conn.session, "second-worker") is None
            if waiter is not None:
                await asyncio.sleep(0.15)
                assert not waiter.done()
            # A duplicate notification must not cancel an awaited finalizer twice.
            await get_stream_manager().put(
                run_id, thread_id, Message(topic=b"run:control", data=b"interrupt")
            )
            release.set()
            assert await asyncio.wait_for(task, 3)
            assert exited.is_set()
            if waiter is not None:
                response = await asyncio.wait_for(waiter, 3)
                assert response.status_code == 200 and response.json() is None
                assert response.headers["content-location"].endswith(run_id)
            async with connect() as conn:
                row = await conn.session.get(RunRow, UUID(run_id))
                assert await stopped_event(conn.session, row) is not None
                assert await conn.session.get(RunLeaseRow, row.run_id) is None
            assert not get_stream_manager().get_control_queues(run_id, thread_id)
        finally:
            release.set()
            for pending in (task, waiter):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(
                *(p for p in (task, waiter) if p is not None), return_exceptions=True
            )


async def test_cancel_fenced_after_crash_is_not_stop_confirmation(pg_runtime, monkeypatch):
    monkeypatch.setenv("GRAPHHARBOR_RUN_WAIT_TIMEOUT", "0.2")
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        repo = RunRepository()
        async with connect() as conn:
            run = await repo.claim_next(conn.session, "dead-worker")
        assert (await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel")).status_code == 202
        async with connect() as conn:
            row = await conn.session.get(RunRow, run.run_id)
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            lease = await conn.session.get(RunLeaseRow, run.run_id)
            lease.expires_at = row.lease_expires_at
        async with connect() as conn:
            assert await repo.requeue_expired(conn.session) == 1
        response = await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true")
        assert response.status_code == 503
        async with connect() as conn:
            row = await conn.session.get(RunRow, run.run_id)
            assert row.status == "interrupted" and row.lease_owner is None
            assert await stopped_event(conn.session, row) is None
            assert await repo.claim_next(conn.session, "replacement") is None


async def test_same_owner_old_generation_cannot_finalize_or_renew(pg_runtime):
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        _, run_id = await new_run(client, threadless=True)
        repo = RunRepository()
        async with connect() as conn:
            run = await repo.claim_next(conn.session, "reused-owner")
            generation = run.retry_count
            await repo.requeue_for_shutdown(conn.session, run.run_id, "reused-owner", drained=True)
        async with connect() as conn:
            run = await repo.claim_next(conn.session, "reused-owner")
            assert run.retry_count == generation + 1
            assert not await repo.renew(
                conn.session, run.run_id, "reused-owner", generation=generation
            )
            with pytest.raises(RunOwnershipError):
                await repo.finish(
                    conn.session,
                    UUID(run_id),
                    "reused-owner",
                    "success",
                    reason="completed",
                    generation=generation,
                )


async def test_cross_instance_subscription_and_reconnect_recheck(pg_runtime):
    import os

    run_id, thread_id = None, None
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
    remote = redis.from_url(os.environ["REDIS_URI"])
    manager = StreamManager(remote)
    manager.start_mux()
    queue = await manager.add_control_queue(run_id, thread_id)
    try:
        assert await manager.wait_control_ready()
        while not queue.empty():
            queue.get_nowait()
        await get_stream_manager().put(
            run_id, thread_id, Message(topic=b"run:control", data=b"rollback")
        )
        assert (await asyncio.wait_for(queue.get(), 2)).data == b"rollback"
        await manager.aclose_fanout()
        await get_stream_manager().put(
            run_id, thread_id, Message(topic=b"run:control", data=b"interrupt")
        )
        manager.start_mux()
        assert await manager.wait_control_ready()
        assert (await asyncio.wait_for(queue.get(), 2)).data == b"recheck"
        assert (
            _control_action(await manager.aget_control_key(run_id, thread_id, refresh=True))
            == "interrupt"
        )
    finally:
        await manager.remove_control_queue(run_id, thread_id, queue)
        await manager.aclose_fanout()
        await remote.aclose()


@pytest.mark.parametrize("action", ["interrupt", "rollback"])
async def test_registration_window_does_not_start_factory(pg_runtime, monkeypatch, action):
    registered, release = asyncio.Event(), asyncio.Event()
    manager = get_stream_manager()
    original = manager.wait_control_ready

    async def ready():
        registered.set()
        await release.wait()
        return await original()

    monkeypatch.setattr(manager, "wait_control_ready", ready)
    opened = []

    @asynccontextmanager
    async def open_graph(*args):
        opened.append(True)
        yield object()

    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="registration")
        task = asyncio.create_task(worker.run_once())
        try:
            await asyncio.wait_for(registered.wait(), 3)
            assert (
                await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?action={action}")
            ).status_code == 202
            release.set()
            assert await asyncio.wait_for(task, 3)
            assert opened == []
            assert (
                await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true")
            ).status_code == (404 if action == "rollback" else 200)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("action", ["interrupt", "rollback"])
async def test_real_subgraph_stream_join_and_slow_cleanup(pg_runtime, monkeypatch, action):
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph
    from starlette.requests import Request

    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.checkpoint_mutations import rollback_receipt
    from langhost.streaming import _run_sse

    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "2")
    monkeypatch.setenv("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "0.1")
    entered, cleaning, release, exited, renewed = (asyncio.Event() for _ in range(5))

    class State(TypedDict):
        value: int

    async def child_node(state):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            exited.set()
        return state

    child = (
        StateGraph(State)
        .add_node("child", child_node)
        .add_edge(START, "child")
        .add_edge("child", END)
        .compile()
    )
    graph = (
        StateGraph(State)
        .add_node("subgraph", child)
        .add_edge(START, "subgraph")
        .add_edge("subgraph", END)
        .compile(checkpointer=get_checkpointer())
    )

    @asynccontextmanager
    async def open_graph(*args):
        yield graph

    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="real-subgraph")
    original = worker.repository.renew

    async def renew(*args, **kwargs):
        result = await original(*args, **kwargs)
        if cleaning.is_set() and result:
            renewed.set()
        return result

    monkeypatch.setattr(worker.repository, "renew", renew)
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        task = asyncio.create_task(worker.run_once())
        waiting, joining, consuming = None, None, None
        try:
            await asyncio.wait_for(entered.wait(), 5)
            request = Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "headers": [],
                    "app": create_app({"graphs": {}}),
                }
            )
            response = await _run_sse(
                request,
                run_id=UUID(run_id),
                thread_id=UUID(thread_id),
                payload={"stream_resumable": True},
                include_location=True,
                version="v3",
            )
            frames = []

            async def consume():
                async for frame in response.body_iterator:
                    frames.append(frame)

            consuming = asyncio.create_task(consume())
            path = f"/threads/{thread_id}/runs/{run_id}"
            waiting = asyncio.create_task(client.post(path + f"/cancel?wait=true&action={action}"))
            if action == "interrupt":
                joining = asyncio.create_task(client.get(path + "/join"))
            await asyncio.wait_for(cleaning.wait(), 2)
            await asyncio.wait_for(renewed.wait(), 3)
            assert not waiting.done() and not consuming.done()
            if joining:
                assert not joining.done()
            async with connect() as conn:
                assert (
                    await conn.session.scalar(
                        select(RuntimeEventRow).where(
                            RuntimeEventRow.run_id == UUID(run_id),
                            RuntimeEventRow.terminal.is_(True),
                        )
                    )
                    is None
                )
                assert await conn.session.get(RunLeaseRow, UUID(run_id)) is not None
            release.set()
            assert await asyncio.wait_for(task, 5)
            assert exited.is_set()
            assert (await asyncio.wait_for(waiting, 3)).status_code == 200
            await asyncio.wait_for(consuming, 3)
            if joining:
                assert (await joining).status_code == 200
            if action == "rollback":
                async with connect() as conn:
                    receipt = await rollback_receipt(conn.session, UUID(run_id))
                    assert receipt.payload["execution_stopped"] is True
            else:
                assert any('"execution_stopped":true' in frame for frame in frames)
        finally:
            release.set()
            for pending in (task, waiting, joining, consuming):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(
                *(p for p in (task, waiting, joining, consuming) if p is not None),
                return_exceptions=True,
            )


async def test_api_transaction_rollback_does_not_publish_cancel(pg_runtime):
    from langhost.core_api import _cancel_row

    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        async with connect() as conn:
            run = await RunRepository().claim_next(conn.session, "transaction")
        with pytest.raises(RuntimeError, match="abort"):
            async with connect() as conn:
                row = await conn.session.get(RunRow, run.run_id)
                await _cancel_row(None, conn, row, "interrupt")
                raise RuntimeError("abort")
        assert await get_stream_manager().aget_control_key(run_id, thread_id, refresh=True) is None
        async with connect() as conn:
            assert (await conn.session.get(RunRow, UUID(run_id))).status == "running"


async def test_real_pubsub_disconnect_rechecks_cancel_key(pg_runtime, monkeypatch):
    from redis.asyncio.retry import Retry
    from redis.backoff import NoBackoff

    from langgraph_runtime_pg import redis_stream

    remote = redis.from_url(
        os.environ["REDIS_URI"], client_name="cancel-reconnect", retry=Retry(NoBackoff(), 0)
    )
    manager = StreamManager(remote)
    manager.start_mux()
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
    queue = await manager.add_control_queue(run_id, thread_id)
    disconnected, reconnect = asyncio.Event(), asyncio.Event()

    async def reconnect_barrier():
        disconnected.set()
        await reconnect.wait()

    monkeypatch.setattr(redis_stream, "_reconnect_backoff", reconnect_barrier)
    try:
        assert await manager.wait_control_ready()
        while not queue.empty():
            queue.get_nowait()
        connections = [
            row["id"]
            for row in await remote.client_list()
            if row["name"] == "cancel-reconnect" and "P" in row["flags"]
        ]
        assert connections
        for connection_id in connections:
            await remote.client_kill_filter(_id=connection_id)
        await asyncio.wait_for(disconnected.wait(), 3)
        await get_stream_manager().put(
            run_id, thread_id, Message(topic=b"run:control", data=b"interrupt")
        )
        assert queue.empty(), "disconnected subscription received the notification"
        reconnect.set()
        message = await asyncio.wait_for(queue.get(), 3)
        assert message.data == b"recheck"
        assert (
            _control_action(await manager.aget_control_key(run_id, thread_id, refresh=True))
            == "interrupt"
        )
    finally:
        reconnect.set()
        await manager.remove_control_queue(run_id, thread_id, queue)
        await manager.aclose_fanout()
        await remote.aclose()


async def test_stop_confirmation_retries_commit_failure_with_lease(pg_runtime, monkeypatch):
    from asyncpg import PostgresConnectionError

    entered, exited, failed, retry = (asyncio.Event() for _ in range(4))

    @asynccontextmanager
    async def open_graph(*args):
        yield object()

    async def invoke(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="commit-retry")
    original = worker.repository.finish
    attempts = []

    async def finish(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            failed.set()
            raise PostgresConnectionError("isolated terminal commit failure")
        await retry.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(worker.repository, "finish", finish)
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        task = asyncio.create_task(worker.run_once())
        waiting = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            waiting = asyncio.create_task(
                client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true")
            )
            await asyncio.wait_for(failed.wait(), 3)
            assert exited.is_set() and not waiting.done()
            async with connect() as conn:
                run = await conn.session.get(RunRow, UUID(run_id))
                assert await stopped_event(conn.session, run) is None
                assert await worker.repository.renew(
                    conn.session, run.run_id, worker.owner, generation=run.retry_count
                )
            retry.set()
            assert await asyncio.wait_for(task, 3)
            assert (await asyncio.wait_for(waiting, 3)).status_code == 200
            assert len(attempts) == 2
            async with connect() as conn:
                events = list(
                    await conn.session.scalars(
                        select(RuntimeEventRow).where(
                            RuntimeEventRow.run_id == UUID(run_id),
                            RuntimeEventRow.terminal.is_(True),
                        )
                    )
                )
                assert len(events) == 1 and events[0].payload["execution_stopped"] is True
        finally:
            retry.set()
            for pending in (task, waiting):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(p for p in (task, waiting) if p), return_exceptions=True)


async def test_rollback_failure_keeps_stop_receipt_for_durable_retry(pg_runtime, monkeypatch):
    from langgraph_runtime_pg import checkpoint_mutations as mutations

    entered, exited, failed = (asyncio.Event() for _ in range(3))

    @asynccontextmanager
    async def open_graph(*args):
        yield object()

    async def invoke(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)
    original = mutations.rollback_run

    async def rollback(*args, **kwargs):
        if not kwargs.get("validate_only") and kwargs.get("execution_stopped"):
            failed.set()
            raise ConnectionError("isolated rollback restoration failure")
        return await original(*args, **kwargs)

    monkeypatch.setattr(mutations, "rollback_run", rollback)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="rollback-retry")
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        task = asyncio.create_task(worker.run_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            path = f"/threads/{thread_id}/runs/{run_id}/cancel"
            assert (await client.post(path + "?action=rollback")).status_code == 202
            await asyncio.wait_for(failed.wait(), 3)
            assert await asyncio.wait_for(task, 3)
            assert exited.is_set()
            async with connect() as conn:
                run = await conn.session.get(RunRow, UUID(run_id))
                assert run.reason == "rollback"
                assert await mutations.rollback_receipt(conn.session, run.run_id) is None
                assert (
                    await conn.session.scalar(
                        select(RuntimeEventRow).where(
                            RuntimeEventRow.run_id == run.run_id,
                            RuntimeEventRow.topic == "cancel.stopped",
                        )
                    )
                    is not None
                )
                assert await RunRepository().claim_next(conn.session, "next-worker") is None
            # Recovery uses the committed stop evidence, even without the original Worker.
            monkeypatch.setattr(mutations, "rollback_run", original)
            await mutations.complete_rollbacks(run_id=UUID(run_id))
            async with connect() as conn:
                assert await conn.session.get(RunRow, UUID(run_id)) is None
                receipt = await mutations.rollback_receipt(conn.session, UUID(run_id))
                assert receipt.payload["execution_stopped"] is True
                assert receipt.payload["rollback_completed"] is True
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_graph_runtime_preserves_verified_user_attributes(pg_runtime):
    from langgraph.graph import END, START, StateGraph

    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    observed = []

    async def node(state: dict, runtime):
        user = runtime.server_info.user
        observed.append((user.identity, user["team"], user.is_authenticated))
        return state

    graph = (
        StateGraph(dict)
        .add_node("node", node)
        .add_edge(START, "node")
        .add_edge("node", END)
        .compile()
    )
    config = thread_config(
        "verified-thread",
        assistant_id="assistant",
        graph_id="cancel",
        runtime_context={"auth_user": {"identity": "verified", "team": "blue"}},
    )
    events = []

    async def event(value):
        events.append(value)

    await invoke_graph(graph, {}, config=config, on_event=event)
    assert observed == [("verified", "blue", True)]
    assert events


@pytest.mark.parametrize("disconnect", [False, True])
async def test_wait_timeout_or_disconnect_keeps_cancel_and_cleanup_lease(
    pg_runtime, monkeypatch, disconnect
):
    monkeypatch.setenv("GRAPHHARBOR_RUN_WAIT_TIMEOUT", "0.2")
    entered, cleaning, release = (asyncio.Event() for _ in range(3))

    @asynccontextmanager
    async def open_graph(*args):
        yield object()

    async def invoke(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="waiting")
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        task = asyncio.create_task(worker.run_once())
        waiting = None
        try:
            await asyncio.wait_for(entered.wait(), 3)
            path = f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true"
            waiting = asyncio.create_task(client.post(path))
            await asyncio.wait_for(cleaning.wait(), 2)
            if disconnect:
                waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
            else:
                assert (await asyncio.wait_for(waiting, 2)).status_code == 503
            async with connect() as conn:
                run = await conn.session.get(RunRow, UUID(run_id))
                assert run.reason == "cancel_requested" and run.lease_owner == worker.owner
                assert await stopped_event(conn.session, run) is None
            release.set()
            assert await asyncio.wait_for(task, 3)
            assert (await client.post(path)).status_code == 200
        finally:
            release.set()
            for pending in (task, waiting):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(*(p for p in (task, waiting) if p), return_exceptions=True)


async def test_control_publish_failure_uses_pg_polling(pg_runtime, monkeypatch):
    entered, exited = asyncio.Event(), asyncio.Event()
    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "2")

    @asynccontextmanager
    async def open_graph(*args):
        yield object()

    async def invoke(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    manager = get_stream_manager()
    original = manager.put

    async def unavailable(run_id, thread_id, message, **kwargs):
        if b"control" in message.topic:
            raise redis.ConnectionError("isolated control key/publication failure")
        return await original(run_id, thread_id, message, **kwargs)

    monkeypatch.setattr(manager, "put", unavailable)
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="pg-fallback")
    async with AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    ) as client:
        thread_id, run_id = await new_run(client)
        task = asyncio.create_task(worker.run_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            response = await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true")
            assert response.status_code == 200
            assert await asyncio.wait_for(task, 3)
            assert exited.is_set()
            assert await manager.aget_control_key(run_id, thread_id, refresh=True) is None
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def test_concurrent_cancel_latency_and_shared_listener_cleanup(
    pg_runtime, monkeypatch, tmp_path
):
    monkeypatch.delenv("LG_BG_JOB_HEARTBEAT", raising=False)
    remote = redis.from_url(os.environ["REDIS_URI"], client_name="cancel-performance")
    manager = StreamManager(remote)
    manager.start_mux()
    await remote.ping()
    assert await manager.wait_control_ready()
    publisher = get_stream_manager()
    await publisher.wait_control_ready()
    observed = {}
    tasks = []
    ticks = []
    original_add, original_put = manager.add_control_queue, publisher.put

    async def add(run_id, thread_id):
        queue = await original_add(run_id, thread_id)
        original_get = queue.get

        async def get():
            message = await original_get()
            if _control_action(message) in {"interrupt", "rollback"}:
                observed[str(thread_id)]["received"] = time.monotonic()
            return message

        queue.get = get
        return queue

    async def put(run_id, thread_id, message, **kwargs):
        if _control_action(message) in {"interrupt", "rollback"}:
            observed[str(thread_id)]["published"] = time.monotonic()
        return await original_put(run_id, thread_id, message, **kwargs)

    @asynccontextmanager
    async def open_graph(_, cfg):
        sample = observed[cfg["configurable"]["thread_id"]]
        try:
            yield object()
        finally:
            sample["stopped"] = time.monotonic()

    async def invoke(*args, config, **kwargs):
        sample = observed[config["configurable"]["thread_id"]]
        sample["entered"].set()
        await asyncio.Event().wait()

    async def renew(*args, **kwargs):
        ticks.append(time.monotonic())
        return await RunRepository.renew(*args, **kwargs)

    monkeypatch.setattr(manager, "add_control_queue", add)
    monkeypatch.setattr(publisher, "put", put)
    monkeypatch.setattr(
        "langgraph_runtime_pg.production_worker.get_stream_manager", lambda: manager
    )
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", invoke)

    def resources():
        return sum(
            len(queues) for runs in manager.control_queues.values() for queues in runs.values()
        )

    def listeners():
        return sum(
            "P" in row["flags"] for row in connections if row["name"] == "cancel-performance"
        )

    connections = await remote.client_list()
    baseline = listeners()
    mux_tasks = tuple(manager._mux_tasks)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
        ) as client:
            for batch in range(3):
                runs = [await new_run(client) for _ in range(8)]
                for thread_id, run_id in runs:
                    observed[thread_id] = {"run_id": run_id, "entered": asyncio.Event()}
                    worker = ProductionWorker(
                        SimpleNamespace(open=open_graph), owner=f"latency-{batch}-{thread_id}"
                    )
                    original_fanout = worker._fanout_durable_event

                    async def fanout(event, original=original_fanout):
                        if event.terminal:
                            observed[str(event.thread_id)]["committed"] = time.monotonic()
                        await original(event)

                    monkeypatch.setattr(worker, "_fanout_durable_event", fanout)
                    monkeypatch.setattr(
                        worker.repository,
                        "renew",
                        lambda *a, repo=worker.repository, **k: renew(repo, *a, **k),
                    )
                    tasks.append(asyncio.create_task(worker.run_once()))
                await asyncio.wait_for(
                    asyncio.gather(*(observed[tid]["entered"].wait() for tid, _ in runs)), 10
                )
                assert resources() == 8 and tuple(manager._mux_tasks) == mux_tasks
                connections = await remote.client_list()
                assert listeners() == baseline
                responses = await asyncio.gather(
                    *(
                        client.post(f"/threads/{tid}/runs/{rid}/cancel?wait=true")
                        for tid, rid in runs
                    )
                )
                assert all(response.status_code == 200 for response in responses)
                assert all(await asyncio.wait_for(asyncio.gather(*tasks), 10))
                assert resources() == 0
        assert ticks == [], "healthy notification waited for heartbeat"
        assert not any(
            task.get_name().startswith(("control-", "heartbeat-", "cancel-", "shutdown-", "run-"))
            for task in asyncio.all_tasks()
        )
        values = list(observed.values())
        latency = {}
        for name, start, end in (
            ("publish_to_receive", "published", "received"),
            ("receive_to_stop", "received", "stopped"),
            ("stop_to_commit", "stopped", "committed"),
        ):
            timings = sorted((sample[end] - sample[start]) * 1000 for sample in values)
            assert timings[0] >= 0
            latency[name] = {
                "p50_ms": round(statistics.median(timings), 3),
                "p95_ms": round(timings[math.ceil(len(timings) * 0.95) - 1], 3),
            }
        connections = await remote.client_list()
        assert listeners() == baseline
        (tmp_path / "cancel-performance.json").write_text(
            json.dumps(
                {
                    "samples": len(values),
                    "concurrency": 8,
                    "failures": 0,
                    "heartbeat_ticks": len(ticks),
                    "latency": latency,
                    "redis_pubsub_connections_before": baseline,
                    "redis_pubsub_connections_after": listeners(),
                    "control_queues_after": resources(),
                    "managed_tasks_after": 0,
                },
                indent=2,
            )
            + "\n"
        )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await manager.aclose_fanout()
        await remote.aclose()


async def test_cancel_authorization_and_thread_scope_have_no_side_effects(pg_runtime, monkeypatch):
    from langgraph_sdk import Auth

    from langhost import server

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "isolated-cancel-auth-secret")
    auth = Auth()

    @auth.authenticate
    async def authenticate(authorization=None):
        if not authorization:
            raise Auth.exceptions.HTTPException(status_code=401)
        return {"identity": authorization}

    @auth.on.threads
    async def authorize(ctx, value):
        value.setdefault("metadata", {}).update(owner=ctx.user.identity)
        return {"owner": ctx.user.identity}

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"authorization": "alice"}
    ) as client:
        thread_id, run_id = await new_run(client)
        other_thread, _ = await new_run(client)
        path = f"/threads/{thread_id}/runs/{run_id}/cancel?wait=true"
        assert (await client.post(path, headers={"authorization": ""})).status_code == 401
        assert (await client.post(path, headers={"authorization": "bob"})).status_code == 404
        assert (
            await client.post(f"/threads/{other_thread}/runs/{run_id}/cancel")
        ).status_code == 404
        assert await get_stream_manager().aget_control_key(run_id, thread_id, refresh=True) is None
        async with connect() as conn:
            assert (await conn.session.get(RunRow, UUID(run_id))).status == "pending"
        assert (await client.post(path)).status_code == 200
        control = await get_stream_manager().aget_control_key(run_id, thread_id, refresh=True)
        assert json.loads(control.data) == {"action": "interrupt"}
