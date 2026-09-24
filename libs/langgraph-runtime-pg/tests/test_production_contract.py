from __future__ import annotations

import asyncio
import contextlib
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def _open_fake_graph(_graph_id: str, _config: object):
    yield object()


def test_run_state_maps_cancel_and_hitl_to_interrupted() -> None:
    from langgraph_runtime_pg.protocol import RunReason, RunStatus, protocol_event
    from langgraph_runtime_pg.run_state import transition

    cancel = transition(
        RunStatus.RUNNING,
        RunStatus.INTERRUPTED,
        reason=RunReason.CANCEL_REQUESTED,
    )
    hitl = transition(
        RunStatus.RUNNING,
        RunStatus.INTERRUPTED,
        reason=RunReason.HITL_INTERRUPT,
    )
    assert cancel.status == hitl.status == RunStatus.INTERRUPTED
    assert cancel.reason != hitl.reason
    cancel_event = protocol_event(
        event_id="cancel-1",
        sequence=1,
        run_id="run-1",
        thread_id="thread-1",
        event={
            "event": "lifecycle",
            "status": RunStatus.INTERRUPTED.value,
            "reason": RunReason.CANCEL_REQUESTED.value,
        },
    )
    hitl_event = protocol_event(
        event_id="hitl-1",
        sequence=2,
        run_id="run-1",
        thread_id="thread-1",
        event={
            "event": "lifecycle",
            "status": RunStatus.INTERRUPTED.value,
            "reason": RunReason.HITL_INTERRUPT.value,
        },
    )
    assert cancel_event["params"]["data"]["reason"] == "cancel_requested"
    assert hitl_event["params"]["data"]["reason"] == "hitl_interrupt"


def test_run_state_rejects_cancelled_status_and_invalid_success_reason() -> None:
    from langgraph_runtime_pg.run_state import InvalidTransition, transition

    with pytest.raises(ValueError):
        transition("running", "cancelled", reason="cancel_requested")
    with pytest.raises(InvalidTransition):
        transition("running", "success", reason="business_error")

    assert (
        transition("interrupted", "interrupted", reason="cancel_requested").status.value
        == "interrupted"
    )


def test_run_configurable_does_not_accept_client_identity_overrides() -> None:
    from types import SimpleNamespace

    from langgraph_runtime_pg.ops import _build_run_configurable

    result = _build_run_configurable(
        {
            "configurable": {
                "user_id": "client-user",
                "tenant_id": "client-tenant",
                "model_id": "model-a",
            }
        },
        SimpleNamespace(config={"configurable": {"user_id": "thread-user"}}),
        SimpleNamespace(
            graph_id="assistant",
            config={"configurable": {"user_id": "assistant-user"}},
        ),
        uuid4(),
        uuid4(),
        uuid4(),
        "trusted-user",
    )

    assert result["user_id"] == "trusted-user"
    assert "tenant_id" not in result
    assert "project_id" not in result
    assert "role" not in result
    assert "permissions" not in result
    assert result["model_id"] == "model-a"


@pytest.mark.asyncio
async def test_worker_loop_survives_transient_infrastructure_failure(monkeypatch) -> None:
    from types import SimpleNamespace

    from langgraph_runtime_pg.production_worker import ProductionWorker

    worker = ProductionWorker(SimpleNamespace(), owner="transient-failure")
    attempts = 0

    async def run_once() -> bool:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("database restarting")
        worker.stop_event.set()
        return True

    monkeypatch.setattr(worker, "run_once", run_once)
    await asyncio.wait_for(worker.run_forever(), timeout=3)
    assert attempts == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("concurrency", [1, 4])
async def test_run_worker_starts_independent_slots(monkeypatch, concurrency: int) -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from langgraph_runtime_pg import production_worker as module
    from langgraph_runtime_pg.production_worker import ProductionWorker

    ready = asyncio.Event()
    started: list[ProductionWorker] = []
    stopped = False
    registry = SimpleNamespace(attach_checkpointer=lambda _: None)

    async def start_pool() -> None:
        return None

    async def stop_pool() -> None:
        nonlocal stopped
        stopped = True

    async def run_forever(worker: ProductionWorker) -> None:
        started.append(worker)
        if len(started) == concurrency:
            ready.set()
        await ready.wait()

    monkeypatch.setattr(module.GraphRegistry, "from_path", lambda _: registry)
    monkeypatch.setattr(module, "start_pool", start_pool)
    monkeypatch.setattr(module, "stop_pool", stop_pool)
    monkeypatch.setattr(module, "get_checkpointer", lambda: object())
    monkeypatch.setattr(module.ProductionWorker, "run_forever", run_forever)
    await asyncio.wait_for(
        module.run_worker(Path("unused.json"), n_jobs_per_worker=concurrency), timeout=2
    )

    assert stopped
    assert len({worker.owner for worker in started}) == concurrency
    assert len({id(worker.repository) for worker in started}) == concurrency
    assert sum(worker.enable_reaper for worker in started) == 1


@pytest.mark.asyncio
async def test_run_worker_rejects_invalid_concurrency() -> None:
    from pathlib import Path

    from langgraph_runtime_pg.production_worker import run_worker

    with pytest.raises(ValueError, match="positive integer"):
        await run_worker(Path("unused.json"), n_jobs_per_worker=0)


@pytest.mark.asyncio
async def test_stream_event_buffer_batches_without_dropping_or_reordering() -> None:
    from langgraph_runtime_pg.production_worker import _is_message_delta, _StreamEventBuffer

    assert _is_message_delta({"event": "messages", "data": [{"event": "content-block-delta"}, {}]})
    assert not _is_message_delta({"event": "messages", "data": [{"event": "message-end"}, {}]})

    batches: list[list[int]] = []

    async def publish(events: list[dict]) -> None:
        batches.append([event["seq"] for event in events])

    buffer = _StreamEventBuffer(publish)
    for sequence in range(33):
        await buffer.add({"seq": sequence})
    await buffer.flush()

    assert batches == [list(range(32)), [32]]


@pytest.mark.asyncio
async def test_stream_event_buffer_reports_timer_commit_failure() -> None:
    from langgraph_runtime_pg.production_worker import _StreamEventBuffer

    async def publish(_events: list[dict]) -> None:
        raise OSError("database unavailable")

    buffer = _StreamEventBuffer(publish)
    await buffer.add({"seq": 1})
    await asyncio.sleep(0.07)
    with pytest.raises(OSError, match="database unavailable"):
        await buffer.flush()


@pytest.mark.asyncio
async def test_worker_event_batch_fanout_follows_commit(monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    from langgraph_runtime_pg import production_worker as module

    worker = module.ProductionWorker(SimpleNamespace(), owner="batch-test")
    committed = False
    transactions = 0
    published: list[int] = []

    @asynccontextmanager
    async def connect():
        nonlocal committed, transactions
        transactions += 1
        yield SimpleNamespace(session=object())
        committed = True

    async def record_message_deltas(_session, *, events, run_id, thread_id, **_kwargs):
        assert not committed
        return [
            SimpleNamespace(
                run_id=run_id,
                thread_id=thread_id,
                event_id=uuid4(),
                topic="messages",
                payload=event,
                sequence=event["seq"],
            )
            for event in events
        ]

    async def put_batch(_run_id, _thread_id, messages):
        assert committed
        published.extend(json.loads(run_message.data)["seq"] for run_message, _ in messages)

    monkeypatch.setattr(module, "connect", connect)
    monkeypatch.setattr(worker.repository, "record_message_deltas", record_message_deltas)
    monkeypatch.setattr(module, "get_stream_manager", lambda: SimpleNamespace(put_batch=put_batch))
    await worker._publish_events(
        uuid4(),
        uuid4(),
        [
            {"event": "messages", "data": [{"event": "content-block-delta"}], "seq": seq}
            for seq in range(32)
        ],
    )

    assert transactions == 1
    assert published == list(range(32))


@pytest.mark.asyncio
async def test_four_worker_slots_execute_distinct_threads_together(pg_runtime, monkeypatch) -> None:
    from types import SimpleNamespace

    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_ids = [uuid4() for _ in range(4)]
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="concurrent-test",
                config={},
                context={},
                metadata_={},
            )
        )
        for thread_id in thread_ids:
            conn.session.add(
                ThreadRow(
                    thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={}
                )
            )
            await RunRepository().create(
                conn.session,
                assistant_id=assistant_id,
                thread_id=thread_id,
                kwargs={"input": {}},
                metadata={},
                tenant_id=None,
                project_id=None,
            )

    active = 0
    maximum = 0
    all_started = asyncio.Event()

    async def fake_invoke(*_args, **_kwargs):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        if active == 4:
            all_started.set()
        await all_started.wait()
        active -= 1
        return SimpleNamespace(value={"ok": True}, interrupts=())

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    workers = [
        ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner=f"slot-{index}")
        for index in range(4)
    ]
    assert all(await asyncio.wait_for(asyncio.gather(*(w.run_once() for w in workers)), 5))
    assert maximum == 4
    async with connect() as conn:
        rows = (await conn.session.scalars(select(RunRow))).all()
    assert len(rows) == 4 and all(row.status == "success" for row in rows)


@pytest.mark.asyncio
async def test_multi_slot_shutdown_requeues_all_inflight_runs(pg_runtime, monkeypatch) -> None:
    from types import SimpleNamespace

    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="shutdown-test",
                config={},
                context={},
                metadata_={},
            )
        )
        for _ in range(4):
            thread_id = uuid4()
            conn.session.add(ThreadRow(thread_id=thread_id))
            await RunRepository().create(
                conn.session,
                assistant_id=assistant_id,
                thread_id=thread_id,
                kwargs={"input": {}},
                metadata={},
                tenant_id=None,
                project_id=None,
            )

    started = asyncio.Event()
    count = 0

    async def fake_invoke(*_args, **_kwargs):
        nonlocal count
        count += 1
        if count == 4:
            started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "2")
    workers = [
        ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner=f"shutdown-slot-{index}")
        for index in range(4)
    ]
    tasks = [asyncio.create_task(worker.run_once()) for worker in workers]
    await asyncio.wait_for(started.wait(), 4)
    for worker in workers:
        worker.stop_event.set()
    assert all(await asyncio.wait_for(asyncio.gather(*tasks), 4))

    async with connect() as conn:
        rows = (await conn.session.scalars(select(RunRow))).all()
    assert len(rows) == 4
    assert all(row.status == "pending" and row.lease_owner is None for row in rows)


@pytest.mark.asyncio
async def test_worker_preserves_v3_message_deltas_before_terminal(pg_runtime, monkeypatch) -> None:
    import json
    from types import SimpleNamespace

    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.redis_stream import get_stream_manager
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id, thread_id = uuid4(), uuid4()
    count = int(os.environ.get("GRAPHHARBOR_TEST_DELTA_COUNT", "100"))
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="delta-test",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    async def fake_invoke(*_args, on_event, **_kwargs):
        for index in range(count):
            await on_event(
                {
                    "event": "messages",
                    "data": [
                        {"event": "content-block-delta", "delta": {"text": str(index)}},
                        {},
                    ],
                }
            )
        await on_event({"event": "values", "data": {"done": True}})
        return SimpleNamespace(value={"done": True}, interrupts=())

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    worker = ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner="delta-test")
    thread_events = asyncio.Queue()
    get_stream_manager().thread_streams[thread_id].append(thread_events)
    assert await worker.run_once()

    async with connect() as conn:
        events = (
            await conn.session.scalars(
                select(RuntimeEventRow)
                .where(RuntimeEventRow.run_id == run_id)
                .order_by(RuntimeEventRow.sequence)
            )
        ).all()
    deltas = [event for event in events if event.topic == "messages"]
    assert [event.payload["data"][0]["delta"]["text"] for event in deltas] == [
        str(index) for index in range(count)
    ]
    assert len({event.event_id for event in events}) == len(events)
    assert [event.sequence for event in events] == sorted(event.sequence for event in events)
    assert events[-2].topic == "values"
    assert events[-1].terminal and events[-1].payload["status"] == "success"
    wire_sequences = [
        json.loads(thread_events.get_nowait().data)["seq"] for _ in range(thread_events.qsize())
    ]
    assert wire_sequences == [event.sequence for event in events]


@pytest.mark.asyncio
async def test_batch_fanout_reaches_remote_run_and_thread_subscribers(pg_runtime) -> None:
    import redis.asyncio as redis

    from langgraph_runtime_pg.redis_stream import Message, StreamManager, get_stream_manager

    run_id, thread_id = uuid4(), uuid4()
    remote = StreamManager(redis.Redis.from_url(os.environ["REDIS_URI"]))
    run_events = asyncio.Queue()
    thread_events = asyncio.Queue()
    remote.get_queues(run_id, thread_id).append(run_events)
    remote.thread_streams[thread_id].append(thread_events)
    remote.start_mux()
    try:
        await asyncio.sleep(0.1)
        await get_stream_manager().put_batch(
            run_id,
            thread_id,
            [
                (
                    Message(topic=b"event:messages", data=str(index).encode()),
                    Message(topic=b"protocol:messages", data=str(index).encode()),
                )
                for index in range(3)
            ],
        )
        assert [(await asyncio.wait_for(run_events.get(), 2)).data for _ in range(3)] == [
            b"0",
            b"1",
            b"2",
        ]
        assert [(await asyncio.wait_for(thread_events.get(), 2)).data for _ in range(3)] == [
            b"0",
            b"1",
            b"2",
        ]
    finally:
        await remote.aclose_fanout()
        await remote._redis.aclose()


@pytest.mark.asyncio
async def test_cancelled_database_scope_returns_connection_to_pool(pg_runtime) -> None:
    from sqlalchemy import text

    import langgraph_runtime_pg.database as database

    async def blocked_query() -> None:
        async with database.connect() as conn:
            await conn.session.execute(text("SELECT pg_sleep(30)"))

    task = asyncio.create_task(blocked_query())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert database._ENGINE is not None
    assert database._ENGINE.pool.checkedout() == 0
    await asyncio.wait_for(database.healthcheck(), timeout=3)


@pytest.mark.asyncio
async def test_anyio_cancelled_database_scope_returns_connection_to_pool(pg_runtime) -> None:
    import anyio
    from sqlalchemy import text

    import langgraph_runtime_pg.database as database

    async def blocked_query() -> None:
        async with database.connect() as conn:
            await conn.session.execute(text("SELECT pg_sleep(30)"))

    with anyio.CancelScope() as scope:
        task = asyncio.create_task(blocked_query())
        await asyncio.sleep(0.1)
        scope.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert database._ENGINE is not None
    assert database._ENGINE.pool.checkedout() == 0
    await asyncio.wait_for(database.healthcheck(), timeout=3)


@pytest.mark.asyncio
async def test_worker_requeues_and_refreshes_checkpointer_after_postgres_restart(
    pg_runtime, monkeypatch
) -> None:
    from types import SimpleNamespace

    from psycopg.errors import AdminShutdown

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id, thread_id = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="postgres-restart",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    async def failed_invoke(*_args, **_kwargs):
        raise AdminShutdown("database restarted")

    refreshed = object()
    attached: list[object] = []
    registry = SimpleNamespace(open=_open_fake_graph, attach_checkpointer=attached.append)
    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", failed_invoke)
    monkeypatch.setattr(
        "langgraph_runtime_pg.production_worker.reconnect_checkpointer",
        lambda: _value(refreshed),
    )
    worker = ProductionWorker(registry, owner="postgres-restart")
    assert await worker.run_once()

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
    assert row is not None
    assert row.status == RunStatus.PENDING.value
    assert row.reason == RunReason.RETRY.value
    assert attached == [refreshed]


async def _value(value):
    return value


def test_protocol_namespace_depth_is_a_maximum() -> None:
    from langhost.protocol_api import _namespace_matches

    assert _namespace_matches(["child"], [["child"]], 0)
    assert _namespace_matches(["child", "tool"], [["child"]], 1)
    assert not _namespace_matches(["child", "tool", "nested"], [["child"]], 1)


def test_v3_projection_normalizes_typed_channels_and_lifecycle() -> None:
    from langgraph_runtime_pg.protocol import project_v3_event

    for method, data in (
        ("messages", [{"event": "content-block-delta", "delta": {"type": "text-delta"}}]),
        ("values", {"value": 1}),
        ("updates", {"node": {"value": 2}}),
        ("custom", {"progress": 0.5}),
        ("tools", {"event": "tool-started", "id": "tool-1"}),
    ):
        projected = project_v3_event(
            {"method": method, "params": {"namespace": ["child:run-1"], "data": data}},
            sequence=7,
        )
        assert projected["seq"] == 7
        assert projected["method"] == method
        assert projected["params"]["namespace"] == ["child:run-1"]
        assert isinstance(projected["params"]["timestamp"], int)
        assert projected["params"]["data"] == data

    lifecycle = project_v3_event(
        {"event": "lifecycle", "status": "interrupted", "reason": "hitl_interrupt"},
        sequence=8,
    )
    assert lifecycle["params"]["data"] == {
        "event": "interrupted",
        "status": "interrupted",
        "reason": "hitl_interrupt",
    }
    input_event = project_v3_event(
        {"event": "input.requested", "data": {"interrupt_id": "i-1"}},
        sequence=9,
    )
    assert input_event["method"] == "input"
    assert input_event["params"]["data"]["event"] == "requested"


def test_run_sse_v3_preserves_every_standard_stream_channel() -> None:
    from langhost.streaming import _event_frame

    for method, data in (
        ("values", {"value": 1}),
        ("updates", {"node": {"value": 2}}),
        ("messages", [{"content": "token"}, {"langgraph_node": "agent"}]),
        ("custom", {"progress": 1}),
        ("checkpoints", {"values": {"value": 1}}),
        ("tasks", {"name": "node", "result": {"value": 1}}),
        ("debug", {"type": "checkpoint"}),
    ):
        frame = _event_frame(
            {
                "seq": 7,
                "event": {
                    "method": method,
                    "params": {
                        "namespace": ["child:run-1"],
                        "timestamp": 123,
                        "data": data,
                    },
                },
            },
            modes={method},
            stream_subgraphs=True,
            version="v3",
        )
        assert frame is not None
        name, typed, sequence = frame
        assert name == method
        assert sequence == 7
        assert typed["method"] == method
        assert typed["params"]["namespace"] == ["child:run-1"]
        assert typed["params"]["data"] == data


@pytest.mark.skip(reason="DelegationJWTValidator moved to ai-agent-platform business layer")
def test_delegation_principal_and_hs256_validation() -> None:
    from langgraph_runtime_pg.auth import DelegationJWTValidator

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://platform.example",
            "aud": "graphharbor",
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "roles": ["operator"],
            "scope": "threads:read runs:write",
            "jti": "delegation-1",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "secret-secret-secret-secret-secret",
        algorithm="HS256",
    )
    principal = DelegationJWTValidator(
        issuer="https://platform.example",
        audience="graphharbor",
        shared_secret="secret-secret-secret-secret-secret",
        algorithms=("HS256",),
    ).validate(token)
    assert principal.scope_filter() == {"tenant_id": "tenant-1", "project_id": "project-1"}
    assert principal.can("threads:read")


@pytest.mark.skip(reason="RuntimePolicy moved to ai-agent-platform business layer")
def test_production_delegation_requires_runtime_policy() -> None:
    from langgraph_runtime_pg.auth import AuthenticationError, DelegationJWTValidator

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://platform.example",
            "aud": "graphharbor",
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "jti": "delegation-without-policy",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "secret-secret-secret-secret-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="policy claims are required"):
        DelegationJWTValidator(
            issuer="https://platform.example",
            audience="graphharbor",
            shared_secret="secret-secret-secret-secret-secret",
            algorithms=("HS256",),
            require_policy=True,
        ).validate(token)


@pytest.mark.skip(reason="RuntimePolicy moved to ai-agent-platform business layer")
def test_delegation_policy_is_bound_to_principal_and_runtime_context(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import (
        DelegationJWTValidator,
        RuntimeContextError,
        RuntimePolicy,
        sign_runtime_context,
        validate_policy_overrides,
        verify_runtime_context_envelope,
    )

    now = datetime.now(UTC)
    policy_claims = {
        "policy_version": "policy-1",
        "allowed_model_ids": ["model-a"],
        "allowed_tool_names": ["search"],
    }
    token = jwt.encode(
        {
            "iss": "https://platform.example",
            "aud": "graphharbor",
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "jti": "delegation-with-policy",
            **policy_claims,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "secret-secret-secret-secret-secret",
        algorithm="HS256",
    )
    principal = DelegationJWTValidator(
        issuer="https://platform.example",
        audience="graphharbor",
        shared_secret="secret-secret-secret-secret-secret",
        algorithms=("HS256",),
        require_policy=True,
    ).validate(token)
    assert principal.policy == RuntimePolicy("policy-1", ("model-a",), ("search",))

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": ["runs:write"],
        "request_id": "request-1",
        "platform_trace_id": "platform-trace-1",
    }
    signed = sign_runtime_context(
        context,
        run_id="run-1",
        thread_id="thread-1",
        policy=principal.policy,
    )
    restored_context, restored_policy = verify_runtime_context_envelope(
        signed,
        run_id="run-1",
        thread_id="thread-1",
        tenant_id="tenant-1",
        project_id="project-1",
    )
    assert restored_context == context
    assert restored_policy == principal.policy

    with pytest.raises(RuntimeContextError, match="rejects model_id"):
        validate_policy_overrides(principal.policy, configurable={"model_id": "model-b"})
    with pytest.raises(RuntimeContextError, match="rejects tool names"):
        validate_policy_overrides(principal.policy, context={"tools": ["shell"]})


def test_runtime_context_rejects_unknown_nested_and_top_level_claims(monkeypatch) -> None:
    import hashlib
    import hmac

    from langgraph_runtime_pg import auth as auth_module
    from langgraph_runtime_pg.auth import RuntimeContextError, sign_runtime_context

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": [],
    }
    with pytest.raises(RuntimeContextError, match="unknown fields"):
        sign_runtime_context(
            {**context, "unexpected": "value"}, run_id="run-1", thread_id="thread-1"
        )

    token = sign_runtime_context(context, run_id="run-1", thread_id="thread-1")
    encoded, _ = token.split(".", 1)
    claims = auth_module._decode_b64_json(encoded)
    claims["unexpected"] = "value"
    encoded = auth_module._b64_json(claims)
    signature = hmac.new(b"runtime-secret", encoded.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(RuntimeContextError, match="unknown claims"):
        auth_module.verify_runtime_context(
            f"{encoded}.{signature}",
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )


def test_api_principal_producer_preserves_correlation(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import Principal
    from langhost.core_api import _runtime_context

    principal = Principal.from_auth_user(
        {
            "identity": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "role": "operator",
            "permissions": ["runs:write"],
            "request_id": "request-1",
            "platform_trace_id": "platform-trace-1",
        }
    )

    assert _runtime_context({}, principal) == {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": ["runs:write"],
        "auth_user": principal.auth_user,
        "request_id": "request-1",
        "platform_trace_id": "platform-trace-1",
    }


def test_custom_auth_user_is_preserved_in_signed_worker_context(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import (
        Principal,
        sign_runtime_context,
        verify_runtime_context_envelope,
    )
    from langgraph_runtime_pg.graph_executor import thread_config

    user = {
        "identity": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": "runs:write threads:read",
        "runtime_extension": {"context_hash": "sha256:test"},
    }
    principal = Principal.from_auth_user(user)
    context = {
        "user_id": principal.subject,
        "tenant_id": principal.tenant_id,
        "project_id": principal.project_id,
        "role": "operator",
        "permissions": ["runs:write", "threads:read"],
        "auth_user": principal.auth_user,
    }
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    token = sign_runtime_context(context, run_id="run-1", thread_id="thread-1")
    restored = verify_runtime_context_envelope(
        token,
        run_id="run-1",
        thread_id="thread-1",
        tenant_id="tenant-1",
        project_id="project-1",
    )
    config = thread_config("thread-1", runtime_context=restored)

    assert config["configurable"]["langgraph_auth_user"] == user
    assert "auth_user" not in config["configurable"]["__graphharbor_runtime_context"]


@pytest.mark.skip(
    reason="DelegationJWTValidator and JWKSCache moved to ai-agent-platform business layer"
)
def test_delegation_jwt_rejects_algorithm_and_refreshes_rotated_key(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import AuthenticationError, DelegationJWTValidator, JWKSCache

    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://platform.example",
            "aud": "graphharbor",
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "jti": "delegation-algorithm",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        "secret-secret-secret-secret-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthenticationError, match="algorithm is not allowed"):
        DelegationJWTValidator(
            issuer="https://platform.example",
            audience="graphharbor",
            shared_secret="secret-secret-secret-secret-secret",
        ).validate(token)

    cache = JWKSCache("https://keys.example/jwks")
    cache._keys = {"old": {"kid": "old"}}
    cache._expires_at = float("inf")
    monkeypatch.setattr(cache, "_fetch", lambda: {"new": {"kid": "new"}})
    assert cache.get("new") == {"kid": "new"}


def test_runtime_context_is_signed_to_one_run_and_scope(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import (
        RuntimeContextError,
        sign_runtime_context,
        verify_runtime_context,
    )

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": ["runs:write"],
    }
    token = sign_runtime_context(context, run_id="run-1", thread_id="thread-1")
    assert (
        verify_runtime_context(
            token,
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )
        == context
    )

    with pytest.raises(RuntimeContextError, match="does not match"):
        verify_runtime_context(
            token,
            run_id="run-2",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )
    with pytest.raises(RuntimeContextError, match="signature"):
        verify_runtime_context(
            f"{token[:-1]}x",
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )


def test_runtime_context_requires_matching_issuer_and_audience(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import (
        RuntimeContextError,
        sign_runtime_context,
        verify_runtime_context,
    )

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER", "https://platform.example")
    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE", "graphharbor-worker")
    context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": [],
    }
    token = sign_runtime_context(context, run_id="run-1", thread_id="thread-1")
    assert (
        verify_runtime_context(
            token,
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )
        == context
    )

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE", "wrong-audience")
    with pytest.raises(RuntimeContextError, match="issuer or audience"):
        verify_runtime_context(
            token,
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )


def test_runtime_context_does_not_reuse_external_jwt_issuer_and_audience(monkeypatch) -> None:
    from langgraph_runtime_pg.auth import (
        sign_runtime_context,
        verify_runtime_context,
    )

    monkeypatch.setenv("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET", "runtime-secret")
    monkeypatch.setenv("GRAPHHARBOR_JWT_ISSUER", "external-jwt-issuer")
    monkeypatch.setenv("GRAPHHARBOR_JWT_AUDIENCE", "external-jwt-audience")
    context = {
        "user_id": "user-1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "role": "operator",
        "permissions": [],
    }
    token = sign_runtime_context(context, run_id="run-1", thread_id="thread-1")

    monkeypatch.delenv("GRAPHHARBOR_JWT_ISSUER")
    monkeypatch.delenv("GRAPHHARBOR_JWT_AUDIENCE")
    assert (
        verify_runtime_context(
            token,
            run_id="run-1",
            thread_id="thread-1",
            tenant_id="tenant-1",
            project_id="project-1",
        )
        == context
    )


def test_schema_models_include_durable_ownership_and_events() -> None:
    from langgraph_runtime_pg.models import RunLeaseRow, RunRow, RuntimeEventRow, RuntimeSchemaRow

    assert {"reason", "idempotency_key", "lease_owner", "event_seq", "next_attempt_at"} <= {
        column.name for column in RunRow.__table__.columns
    }
    assert RunLeaseRow.__tablename__ == "run_leases"
    assert RuntimeEventRow.__tablename__ == "runtime_events"
    assert "terminal" in {column.name for column in RuntimeEventRow.__table__.columns}
    assert RuntimeSchemaRow.__tablename__ == "runtime_schema"


@pytest.mark.asyncio
async def test_migration_is_repeatable_and_schema_head_is_recorded(pg_runtime) -> None:
    from sqlalchemy import text

    from langgraph_runtime_pg.database import connect, get_database_uri
    from langgraph_runtime_pg.migrate import upgrade_head

    assert upgrade_head(get_database_uri()) == "007_checkpoint_baselines"
    assert upgrade_head(get_database_uri()) == "007_checkpoint_baselines"
    async with connect() as conn:
        revision = await conn.session.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision == "007_checkpoint_baselines"


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_cursor", [0, 1])
async def test_record_event_repairs_stale_sequence_counters(pg_runtime, stale_cursor) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="sequence-repair",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        thread = ThreadRow(
            thread_id=thread_id,
            status="idle",
            metadata_={},
            config={},
            interrupts={},
            event_seq=stale_cursor,
        )
        conn.session.add(thread)
        repo = RunRepository()
        run = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        conn.session.add(
            RuntimeEventRow(
                run_id=run.run_id,
                thread_id=thread_id,
                sequence=stale_cursor + 1,
                topic="lifecycle",
                namespace=[],
                payload={"event": "lifecycle"},
            )
        )
        await conn.session.flush()
        event = await RunRepository().record_event(
            conn.session,
            run_id=run.run_id,
            thread_id=thread_id,
            topic="values",
            payload={"event": "values", "data": {"ok": True}},
        )
        assert event.sequence == stale_cursor + 2
        assert thread.event_seq == stale_cursor + 2
        assert run.event_seq == stale_cursor + 2


@pytest.mark.asyncio
async def test_owned_server_core_resource_flow(pg_runtime) -> None:
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assistant_response = await client.post(
            "/assistants",
            json={"graph_id": "assistant", "name": "default", "metadata": {}},
        )
        assert assistant_response.status_code == 200, assistant_response.text
        assistant_id = assistant_response.json()["assistant_id"]

        thread_response = await client.post("/threads", json={"metadata": {}})
        assert thread_response.status_code == 200, thread_response.text
        thread_id = thread_response.json()["thread_id"]

        run_response = await client.post(
            f"/threads/{thread_id}/runs",
            headers={"idempotency-key": "run-1"},
            json={"assistant_id": assistant_id, "input": {"messages": []}},
        )
        assert run_response.status_code == 201, run_response.text
        run_id = run_response.json()["run_id"]
        assert run_response.json()["status"] == "pending"

        duplicate = await client.post(
            f"/threads/{thread_id}/runs",
            headers={"idempotency-key": "run-1"},
            json={"assistant_id": assistant_id, "input": {"messages": ["ignored"]}},
        )
        assert duplicate.status_code == 201
        assert duplicate.json()["run_id"] == run_id

        rejected = await client.post(
            f"/threads/{thread_id}/runs",
            json={"assistant_id": assistant_id, "multitask_strategy": "reject"},
        )
        assert rejected.status_code == 409, rejected.text

        cancelled = await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "interrupted"
        assert (await client.get(f"/threads/{thread_id}/runs/{run_id}")).json()[
            "status"
        ] == "interrupted"
        from sqlalchemy import select

        from langgraph_runtime_pg.database import connect
        from langgraph_runtime_pg.models import RuntimeEventRow

        async with connect() as conn:
            terminal_event = await conn.session.scalar(
                select(RuntimeEventRow).where(RuntimeEventRow.run_id == UUID(run_id))
            )
        assert terminal_event is not None
        assert terminal_event.payload["status"] == "interrupted"


@pytest.mark.asyncio
async def test_pending_rollback_never_deletes_thread_checkpoints(
    pg_runtime, monkeypatch
) -> None:
    from langhost.server import create_app

    cleanup = []

    async def fake_cleanup(thread_id: str) -> None:
        cleanup.append(thread_id)

    monkeypatch.setattr("langhost.core_api.delete_thread_checkpoints", fake_cleanup)
    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assistant = await client.post(
            "/assistants", json={"graph_id": "assistant", "name": "rollback"}
        )
        thread = await client.post("/threads", json={})
        run = await client.post(
            f"/threads/{thread.json()['thread_id']}/runs",
            json={"assistant_id": assistant.json()["assistant_id"], "input": {"value": 1}},
        )
        thread_id = thread.json()["thread_id"]
        run_id = run.json()["run_id"]
        cancelled = await client.post(f"/threads/{thread_id}/runs/{run_id}/cancel?action=rollback")
        missing = await client.get(f"/threads/{thread_id}/runs/{run_id}")

    assert cancelled.status_code == 200
    assert cancelled.json() == {}
    assert missing.status_code == 404
    assert cleanup == []


@pytest.mark.asyncio
async def test_production_auth_rejects_missing_management_and_scope_override(
    pg_runtime, monkeypatch
) -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from langhost.server import create_app

    secret = "secret-secret-secret-secret-secret"
    monkeypatch.setenv("GRAPHHARBOR_ENV", "production")
    monkeypatch.setenv("GRAPHHARBOR_JWT_ISSUER", "https://platform.example")
    monkeypatch.setenv("GRAPHHARBOR_JWT_AUDIENCE", "graphharbor")
    monkeypatch.setenv("GRAPHHARBOR_JWT_SHARED_SECRET", secret)
    monkeypatch.setenv("GRAPHHARBOR_JWT_ALGORITHMS", "HS256")
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": "https://platform.example",
            "aud": "graphharbor",
            "sub": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "jti": "delegation-auth-boundary",
            "policy_version": "policy-1",
            "allowed_model_ids": ["acceptance:model"],
            "allowed_tool_names": [],
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        secret,
        algorithm="HS256",
    )

    async def principal_route(request: Request) -> JSONResponse:
        return JSONResponse({"tenant_id": request.scope["principal"].tenant_id})

    def authenticate(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise ValueError("missing authorization header")
        claims = jwt.decode(
            authorization.removeprefix("Bearer "),
            secret,
            algorithms=["HS256"],
            issuer="https://platform.example",
            audience="graphharbor",
        )
        return {
            "identity": claims["sub"],
            "tenant_id": claims["tenant_id"],
            "project_id": claims["project_id"],
        }

    # Authentication is supplied by an application hook, never an engine JWT fallback.
    monkeypatch.setattr("langhost.server._load_symbol", lambda *_args: authenticate)
    app = create_app(
        {"graphs": {}, "auth": {"path": "test_auth:authenticate"}},
        custom_app=Starlette(routes=[Route("/internal/principal", principal_route)]),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/threads")
        management = await client.get(
            "/threads", headers={"x-graphharbor-management-key": "management-only"}
        )
        override = await client.post(
            "/threads",
            headers={"authorization": f"Bearer {token}"},
            json={"tenant_id": "other-tenant", "project_id": "project-1"},
        )
        created = await client.post(
            "/threads",
            headers={"authorization": f"Bearer {token}"},
            json={"metadata": {"owned": True}},
        )
        assert created.status_code == 200, created.text
        custom = await client.get(
            "/internal/principal", headers={"authorization": f"Bearer {token}"}
        )
        other_token = jwt.encode(
            {
                "iss": "https://platform.example",
                "aud": "graphharbor",
                "sub": "user-2",
                "tenant_id": "tenant-2",
                "project_id": "project-1",
                "jti": "delegation-auth-other",
                "policy_version": "policy-1",
                "allowed_model_ids": ["acceptance:model"],
                "allowed_tool_names": [],
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            secret,
            algorithm="HS256",
        )
        hidden = await client.get(
            f"/threads/{created.json()['thread_id']}",
            headers={"authorization": f"Bearer {other_token}"},
        )
    assert missing.status_code == 401
    assert management.status_code == 403
    assert override.status_code == 403
    assert created.status_code == 200
    assert custom.status_code == 200 and custom.json() == {"tenant_id": "tenant-1"}
    assert hidden.status_code == 404


def test_production_custom_auth_does_not_require_builtin_jwt_config(monkeypatch) -> None:
    import langhost.server as server

    class CustomAuth:
        async def _authenticate_handler(self, authorization: str | None = None) -> dict:
            del authorization
            return {"identity": "custom-user"}

    monkeypatch.setenv("GRAPHHARBOR_ENV", "production")
    monkeypatch.delenv("GRAPHHARBOR_JWT_ISSUER", raising=False)
    monkeypatch.delenv("GRAPHHARBOR_JWT_AUDIENCE", raising=False)
    monkeypatch.setattr(server, "_load_symbol", lambda *_args: CustomAuth())

    assert server.create_app({"graphs": {}, "auth": {"path": "auth.py:auth"}}) is not None


@pytest.mark.asyncio
async def test_langgraph_json_cors_configuration_reaches_asgi_boundary(pg_runtime) -> None:
    from langhost.server import create_app

    app = create_app(
        {
            "graphs": {},
            "http": {
                "cors": {
                    "allow_origins": ["https://frontend.example"],
                    "allow_methods": ["POST"],
                    "allow_headers": ["Authorization", "Content-Type"],
                    "allow_credentials": False,
                }
            },
        }
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.options(
            "/threads",
            headers={
                "Origin": "https://frontend.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example"
    assert "POST" in response.headers["access-control-allow-methods"]


@pytest.mark.asyncio
async def test_run_repository_claim_renew_and_terminal_transition(pg_runtime) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=None,
            kwargs={"input": {}},
            metadata={},
            tenant_id="tenant-1",
            project_id="project-1",
            idempotency_key="repo-run-1",
        )
        run_id = run.run_id

    async with connect() as conn:
        repo = RunRepository(lease_seconds=5)
        claimed = await repo.claim_next(conn.session, "worker-a")
        assert claimed is not None and claimed.run_id == run_id
        assert claimed.status == RunStatus.RUNNING.value
        assert await repo.renew(conn.session, run_id, "worker-a") is True
        finished = await repo.finish(
            conn.session,
            run_id,
            "worker-a",
            RunStatus.SUCCESS,
            reason=RunReason.COMPLETED,
        )
        assert finished is not None and finished.status == RunStatus.SUCCESS.value
        assert (
            await repo.finish(
                conn.session,
                run_id,
                "late-worker",
                RunStatus.ERROR,
                reason=RunReason.BUSINESS_ERROR,
            )
            is None
        )


@pytest.mark.asyncio
async def test_run_repository_requeues_claim_for_graceful_shutdown(pg_runtime) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="shutdown",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        repo = RunRepository(lease_seconds=5)
        run = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    async with connect() as conn:
        assert await repo.claim_next(conn.session, "worker-a") is not None
        requeued = await repo.requeue_for_shutdown(conn.session, run_id, "worker-a")
        assert requeued.status == RunStatus.PENDING.value
        assert requeued.reason == RunReason.SHUTDOWN_REQUEUE.value
        assert requeued.lease_owner is None
        thread = await conn.session.get(ThreadRow, thread_id)
        assert thread is not None and thread.status == "idle"


@pytest.mark.asyncio
async def test_run_repository_does_not_claim_two_runs_on_one_thread(pg_runtime) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        repo = RunRepository()
        first = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {"value": 1}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        second = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {"value": 2}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )

    async with connect() as conn:
        assert await repo.claim_next(conn.session, "worker-a") is not None
    async with connect() as conn:
        assert await repo.claim_next(conn.session, "worker-b") is None
        second_row = await conn.session.get(type(second), second.run_id)
        assert second_row is not None and second_row.status == RunStatus.PENDING.value
        first_row = await conn.session.get(type(first), first.run_id)
        assert first_row is not None and first_row.status == RunStatus.RUNNING.value


@pytest.mark.asyncio
async def test_run_repository_retries_with_backoff_and_reclaims_expired_lease(
    pg_runtime, monkeypatch
) -> None:
    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    monkeypatch.setenv("GRAPHHARBOR_RETRY_BASE_SECONDS", "1")
    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    repo = RunRepository(lease_seconds=5)
    async with connect() as conn:
        claimed = await repo.claim_next(conn.session, "worker-a")
        assert claimed is not None
        await repo.fail(
            conn.session,
            run_id,
            "worker-a",
            infrastructure=True,
            reason=RunReason.INFRASTRUCTURE_ERROR,
        )
        assert claimed.status == RunStatus.PENDING.value
        assert claimed.reason == RunReason.RETRY.value
        assert claimed.next_attempt_at is not None
        assert await repo.claim_next(conn.session, "worker-b") is None

    async with connect() as conn:
        row = await conn.session.get(type(claimed), run_id)
        assert row is not None
        row.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        row.status = RunStatus.RUNNING.value
        row.lease_owner = "dead-worker"
        thread = await conn.session.get(ThreadRow, thread_id)
        assert thread is not None
        thread.status = "busy"
        await conn.session.flush()
        assert await repo.requeue_expired(conn.session) == 1
        assert row.status == RunStatus.PENDING.value
        assert row.next_attempt_at is not None
        assert thread.status == "idle"
        event = await conn.session.scalar(
            select(RuntimeEventRow).where(RuntimeEventRow.run_id == run_id)
        )
        assert event is not None and event.payload["event"] == "lifecycle"

        row.status = RunStatus.RUNNING.value
        row.retry_count = 3
        row.lease_owner = "worker-c"
        row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=5)
        terminal = await repo.fail(
            conn.session,
            run_id,
            "worker-c",
            infrastructure=True,
            reason=RunReason.INFRASTRUCTURE_ERROR,
        )
        assert terminal.status == RunStatus.ERROR.value
        assert terminal.next_attempt_at is None


@pytest.mark.asyncio
async def test_production_worker_honors_database_cancel_without_redis_control(
    pg_runtime, monkeypatch
) -> None:
    import asyncio
    from types import SimpleNamespace

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {"value": 1}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    started = asyncio.Event()

    async def fake_invoke(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)
        return SimpleNamespace(value={"ok": True}, interrupts=())

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "2")
    worker = ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner="cancel-worker")
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=3)

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row is not None
        row.status = RunStatus.INTERRUPTED.value
        row.reason = RunReason.CANCEL_REQUESTED.value

    assert await asyncio.wait_for(task, timeout=4)
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row is not None and row.status == RunStatus.INTERRUPTED.value


@pytest.mark.asyncio
async def test_production_worker_persists_one_timeout_terminal_event(
    pg_runtime, monkeypatch
) -> None:
    from types import SimpleNamespace

    from sqlalchemy import func, select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import (
        AssistantRow,
        RunLeaseRow,
        RunRow,
        RuntimeEventRow,
        ThreadRow,
    )
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="timeout",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    async def fake_invoke(*_args, **_kwargs):
        await asyncio.sleep(30)

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "0.01")
    worker = ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner="timeout-worker")
    assert await asyncio.wait_for(worker.run_once(), timeout=3)

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        thread = await conn.session.get(ThreadRow, thread_id)
        lease = await conn.session.get(RunLeaseRow, run_id)
        terminal_count = await conn.session.scalar(
            select(func.count())
            .select_from(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id, RuntimeEventRow.terminal.is_(True))
        )
        terminal = await conn.session.scalar(
            select(RuntimeEventRow).where(
                RuntimeEventRow.run_id == run_id, RuntimeEventRow.terminal.is_(True)
            )
        )

    assert row is not None and row.status == RunStatus.TIMEOUT.value
    assert row.reason == RunReason.TIMEOUT.value
    assert thread is not None and thread.status == "idle"
    assert lease is None
    assert terminal_count == 1
    assert terminal is not None and terminal.payload["status"] == RunStatus.TIMEOUT.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "thread_limit,run_limit,expected",
    [(None, None, "error"), (3, None, "success"), (3, 1, "error")],
)
async def test_worker_preserves_recursion_limit(pg_runtime, thread_limit, run_limit, expected):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from langgraph.graph import END, START, StateGraph
    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.run_store import RunRepository

    builder = StateGraph(dict)
    builder.add_node("first", lambda state: state)
    builder.add_node("second", lambda state: state)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    @asynccontextmanager
    async def open_graph(_graph_id, config):
        assert config["recursion_limit"] == (run_limit or thread_limit or 1)
        yield builder.compile()

    assistant_id, thread_id = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="limit",
                name="limit",
                config={"recursion_limit": 1},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(
                thread_id=thread_id,
                status="idle",
                metadata_={},
                config={"recursion_limit": thread_limit} if thread_limit else {},
                interrupts={},
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={
                "input": {"ok": True},
                "version": "v3",
                "config": {"recursion_limit": run_limit} if run_limit else {},
            },
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="limit-worker")
    assert await worker.run_once()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        terminal = (
            await conn.session.scalars(
                select(RuntimeEventRow).where(
                    RuntimeEventRow.run_id == run_id, RuntimeEventRow.terminal.is_(True)
                )
            )
        ).all()
    assert row is not None and row.status == expected
    assert len(terminal) == 1 and terminal[0].payload["status"] == expected
    if expected == "error":
        assert "recursion" in str(terminal[0].payload["error"]).lower()


@pytest.mark.asyncio
async def test_success_commit_failure_never_publishes_completed(pg_runtime, monkeypatch) -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.protocol import protocol_event
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id, thread_id = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="commit-fixture",
                name="commit-fixture",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(
                thread_id=thread_id,
                status="idle",
                metadata_={},
                config={},
                interrupts={},
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}, "version": "v3"},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    armed = False
    injected = False
    original_finish, original_commit = RunRepository.finish, AsyncSession.commit

    async def finish(repository, *args, **kwargs):
        nonlocal armed
        result = await original_finish(repository, *args, **kwargs)
        if result is not None and result.status == "success":
            armed = True
        return result

    async def commit(session):
        nonlocal injected
        if armed and not injected:
            injected = True
            raise ConnectionError("injected success commit failure")
        return await original_commit(session)

    monkeypatch.setattr(RunRepository, "finish", finish)
    monkeypatch.setattr(AsyncSession, "commit", commit)
    monkeypatch.setattr(
        "langgraph_runtime_pg.production_worker.invoke_graph",
        AsyncMock(
            return_value=SimpleNamespace(value={"ok": True}, interrupts=()),
        ),
    )
    monkeypatch.setattr(
        "langgraph_runtime_pg.production_worker.reconnect_checkpointer", AsyncMock()
    )
    worker = ProductionWorker(
        SimpleNamespace(
            open=_open_fake_graph,
            attach_checkpointer=lambda _: None,
        ),
        owner="commit-worker",
    )
    published = []

    async def fanout(event):
        published.append(event.payload)

    monkeypatch.setattr(worker, "_fanout_durable_event", fanout)
    assert await worker.run_once()
    assert injected
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row is not None and row.status == "pending"
        events = (
            await conn.session.scalars(
                select(RuntimeEventRow).where(RuntimeEventRow.run_id == run_id)
            )
        ).all()
    assert not any(event.terminal for event in events)
    for payload in published + [event.payload for event in events]:
        wire = protocol_event(
            event_id="probe",
            sequence=1,
            run_id=str(run_id),
            thread_id=str(thread_id),
            event=payload,
        )
        if wire["method"] == "lifecycle":
            assert wire["params"]["data"]["event"] == "running"


@pytest.mark.asyncio
async def test_cancel_publishes_one_durable_terminal_event_and_late_cancel_is_noop(
    pg_runtime,
) -> None:
    from sqlalchemy import func, select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunLeaseRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.core_api import _cancel_row

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="cancel-event",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        repo = RunRepository()
        run = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id
        assert await repo.claim_next(conn.session, "cancel-worker") is not None
        await _cancel_row(None, conn, run, "interrupt")

    async with connect() as conn:
        events = (
            (
                await conn.session.execute(
                    select(RuntimeEventRow)
                    .where(RuntimeEventRow.run_id == run_id)
                    .order_by(RuntimeEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )
        assert len(events) == 1
        assert events[0].payload["status"] == RunStatus.INTERRUPTED.value
        assert events[0].payload["reason"] == "cancel_requested"
        row = await conn.session.get(type(run), run_id)
        assert row is not None and row.status == RunStatus.INTERRUPTED.value
        assert await conn.session.get(RunLeaseRow, run_id) is None
        thread = await conn.session.get(ThreadRow, thread_id)
        assert thread is not None and thread.status == "idle"
        await _cancel_row(None, conn, row, "interrupt")
        await conn.session.flush()

    async with connect() as conn:
        count = await conn.session.scalar(
            select(func.count())
            .select_from(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id)
        )
    assert count == 1


@pytest.mark.asyncio
async def test_cancel_and_finalize_race_keeps_one_terminal_event(pg_runtime) -> None:
    from sqlalchemy import func, select

    from langgraph_runtime_pg.database import connect, get_session_factory
    from langgraph_runtime_pg.models import (
        AssistantRow,
        RunLeaseRow,
        RuntimeEventRow,
        ThreadRow,
    )
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.core_api import _cancel_row

    assistant_id, thread_id = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="race",
                name="race",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        assert await RunRepository().claim_next(conn.session, "race-worker") is not None
        run_id = run.run_id

    async def finalize() -> None:
        async with get_session_factory()() as session, session.begin():
            await RunRepository().finish(
                session,
                run_id,
                "race-worker",
                RunStatus.SUCCESS,
                reason=RunReason.COMPLETED,
            )

    async def cancel() -> None:
        async with connect() as conn:
            row = await conn.session.get(type(run), run_id)
            assert row is not None
            await _cancel_row(None, conn, row, "interrupt")

    await asyncio.gather(finalize(), cancel())

    async with connect() as conn:
        row = await conn.session.get(type(run), run_id)
        terminal_count = await conn.session.scalar(
            select(func.count())
            .select_from(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id, RuntimeEventRow.terminal.is_(True))
        )
        assert row is not None and row.status in {
            RunStatus.SUCCESS.value,
            RunStatus.INTERRUPTED.value,
        }
        assert terminal_count == 1
        assert await conn.session.get(RunLeaseRow, run_id) is None


@pytest.mark.asyncio
async def test_cancel_root_run_persists_terminal_event(pg_runtime) -> None:
    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.core_api import _cancel_row

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="root-cancel",
                name="root-cancel",
                config={},
                context={},
                metadata_={},
            )
        )
        repo = RunRepository()
        run = await repo.create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=None,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        assert await repo.claim_next(conn.session, "root-cancel-worker") is not None
        await _cancel_row(None, conn, run, "interrupt")
        run_id = run.run_id

    async with connect() as conn:
        row = await conn.session.get(type(run), run_id)
        event = await conn.session.scalar(
            select(RuntimeEventRow).where(
                RuntimeEventRow.run_id == run_id,
                RuntimeEventRow.terminal.is_(True),
            )
        )
        assert row is not None and row.status == RunStatus.INTERRUPTED.value
        assert event is not None
        assert event.thread_id is None
        assert event.payload["status"] == RunStatus.INTERRUPTED.value


@pytest.mark.asyncio
async def test_worker_kill_is_recovered_by_lease_reaper(pg_runtime, monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="worker-kill",
                config={},
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository(lease_seconds=5).create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    started = asyncio.Event()

    async def fake_invoke(*_args, **_kwargs):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    worker = ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner="killed-worker")
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), timeout=3)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row is not None and row.status == "running"
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await conn.session.flush()

    assert await worker.reap_once() == 1

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row is not None and row.status == "pending"


@pytest.mark.asyncio
async def test_api_lifespan_restart_keeps_postgres_run_state(pg_runtime) -> None:
    from asgi_lifespan import LifespanManager

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeSchemaRow
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.server import create_app

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="api-restart",
                config={},
                context={},
                metadata_={},
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=None,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run.status = RunStatus.SUCCESS.value
        run_id = run.run_id
        await conn.session.merge(RuntimeSchemaRow(key="contract", value="production-v1"))

    app = create_app({"graphs": {}})
    for _ in range(2):
        async with LifespanManager(app):
            async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
                response = await client.get("/ready")
            assert response.status_code == 200
            async with connect() as conn:
                row = await conn.session.get(type(run), run_id)
                assert row is not None and row.status == RunStatus.SUCCESS.value


@pytest.mark.asyncio
async def test_production_worker_persists_hitl_interrupt_without_lock_deadlock(
    pg_runtime, monkeypatch
) -> None:
    import asyncio
    from types import SimpleNamespace

    from sqlalchemy import select

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RunRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.production_worker import ProductionWorker
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="hitl",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {"value": 1}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run_id = run.run_id

    async def fake_invoke(*_args, **_kwargs):
        return SimpleNamespace(
            value={"partial": True},
            interrupts=(
                SimpleNamespace(id="interrupt-1", value={"question": "approve"}, ns=("child",)),
            ),
        )

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fake_invoke)
    worker = ProductionWorker(SimpleNamespace(open=_open_fake_graph), owner="hitl-worker")
    assert await asyncio.wait_for(worker.run_once(), timeout=3)

    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        thread = await conn.session.get(ThreadRow, thread_id)
        events = (
            (
                await conn.session.execute(
                    select(RuntimeEventRow)
                    .where(RuntimeEventRow.run_id == run_id)
                    .order_by(RuntimeEventRow.sequence)
                )
            )
            .scalars()
            .all()
        )
    assert row is not None and row.status == RunStatus.INTERRUPTED.value
    assert thread is not None and thread.status == "interrupted"
    assert thread.graph_id == "hitl"
    assert thread.interrupts["interrupt-1"]["value"] == {"question": "approve"}
    assert [event.payload["event"] for event in events] == [
        "lifecycle",
        "input.requested",
        "lifecycle",
    ]
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        state = await client.get(f"/threads/{thread_id}/state")
    assert state.status_code == 200
    assert state.json()["interrupts"] == [
        {"id": "interrupt-1", "value": {"question": "approve"}, "ns": ["child"]}
    ]


@pytest.mark.asyncio
async def test_redis_job_hints_are_namespaced_and_fifo(pg_runtime) -> None:
    from langgraph_runtime_pg.redis_stream import dequeue_run_hint, enqueue_run

    while await dequeue_run_hint() is not None:
        pass
    first, second = uuid4(), uuid4()
    await enqueue_run(first)
    await enqueue_run(second)
    assert await dequeue_run_hint() == str(first)
    assert await dequeue_run_hint() == str(second)
    assert await dequeue_run_hint() is None


@pytest.mark.asyncio
async def test_cross_instance_cancel_control_fanout(pg_runtime) -> None:
    import asyncio

    import redis.asyncio as redis

    from langgraph_runtime_pg.redis_stream import Message, StreamManager, get_stream_manager

    run_id, thread_id = uuid4(), uuid4()
    client_b = redis.from_url(os.environ["REDIS_URI"], decode_responses=False)
    manager_b = StreamManager(client_b)
    manager_b.start_mux()
    queue = await manager_b.add_control_queue(run_id, thread_id)
    await asyncio.sleep(0.05)
    await get_stream_manager().put(
        run_id,
        thread_id,
        Message(topic=b"run:control", data=b'{"action":"interrupt"}'),
    )
    message = await asyncio.wait_for(queue.get(), timeout=3)
    assert message.data == b'{"action":"interrupt"}'
    await manager_b.aclose_fanout()
    await client_b.aclose()


@pytest.mark.asyncio
async def test_redis_restart_preserves_postgres_run_state(pg_runtime) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow
    from langgraph_runtime_pg.protocol import RunStatus
    from langgraph_runtime_pg.redis_stream import start_stream, stop_stream
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="restart",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=None,
            kwargs={"input": {}},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run.status = RunStatus.SUCCESS.value
        run_id = run.run_id

    await stop_stream()
    async with connect() as conn:
        row = await conn.session.get(type(run), run_id)
        assert row is not None and row.status == RunStatus.SUCCESS.value
    await start_stream()


@pytest.mark.asyncio
async def test_owned_server_run_sse_replays_durable_events(pg_runtime, monkeypatch) -> None:
    import asyncio

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.server import create_app

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(
                thread_id=thread_id,
                status="idle",
                metadata_={},
                config={},
                interrupts={},
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"stream_mode": "values", "stream_resumable": True},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run.status = RunStatus.SUCCESS.value
        run.reason = RunReason.COMPLETED.value
        run.event_seq = 5
        conn.session.add(
            RuntimeEventRow(
                run_id=run.run_id,
                thread_id=thread_id,
                sequence=5,
                topic="values",
                namespace=[],
                payload={"event": "values", "data": {"value": 2}, "namespace": []},
            )
        )
        run_id = run.run_id

    class FakeManager:
        async def add_queue(self, *_args, **_kwargs):
            return asyncio.Queue()

        async def remove_queue(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: FakeManager())
    monkeypatch.setenv("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_SSE_TIMEOUT_SECONDS", "0.1")
    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/threads/{thread_id}/runs/{run_id}/stream")
        replay = await client.get(
            f"/threads/{thread_id}/runs/{run_id}/stream",
            headers={"last-event-id": "4"},
        )
        expired = await client.get(
            f"/threads/{thread_id}/runs/{run_id}/stream",
            headers={"last-event-id": "1"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: metadata" in response.text
    assert "event: values" in response.text
    assert "id: 5" in response.text
    assert "event: end" not in response.text
    assert "id: 5" in replay.text
    assert "cursor_expired" in expired.text
    assert "run_snapshot" in expired.text
    assert "id: 5" not in expired.text


@pytest.mark.asyncio
async def test_run_sse_v3_returns_raw_typed_protocol_envelopes(pg_runtime, monkeypatch) -> None:
    import asyncio

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.server import create_app

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="v3",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"version": "v3", "stream_mode": "values", "stream_subgraphs": True},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run.status = RunStatus.SUCCESS.value
        run.reason = RunReason.COMPLETED.value
        conn.session.add(
            RuntimeEventRow(
                run_id=run.run_id,
                thread_id=thread_id,
                sequence=1,
                topic="values",
                namespace=["child:run-1"],
                payload={
                    "event": "values",
                    "method": "values",
                    "namespace": ["child:run-1"],
                    "timestamp": 123,
                    "data": {"value": 2},
                },
            )
        )
        run_id = run.run_id

    class FakeManager:
        async def add_queue(self, *_args, **_kwargs):
            return asyncio.Queue()

        async def remove_queue(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: FakeManager())
    monkeypatch.setenv("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_SSE_TIMEOUT_SECONDS", "0.1")
    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/threads/{thread_id}/runs/{run_id}/stream",
            headers={"last-event-id": "0"},
        )

    assert response.status_code == 200
    assert "event: values" in response.text
    assert '"method":"values"' in response.text
    assert '"namespace":["child:run-1"]' in response.text
    assert '"data":{"value":2}' in response.text


@pytest.mark.asyncio
async def test_run_sse_accepts_messages_tuple_mode_alias(pg_runtime, monkeypatch) -> None:
    import asyncio

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository
    from langhost.server import create_app

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="messages",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )
        conn.session.add(
            ThreadRow(thread_id=thread_id, status="idle", metadata_={}, config={}, interrupts={})
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"stream_mode": "messages-tuple"},
            metadata={},
            tenant_id=None,
            project_id=None,
        )
        run.status = RunStatus.SUCCESS.value
        run.reason = RunReason.COMPLETED.value
        conn.session.add(
            RuntimeEventRow(
                run_id=run.run_id,
                thread_id=thread_id,
                sequence=1,
                topic="messages",
                namespace=[],
                payload={"event": "messages", "data": [{"content": "hi"}, {"node": "assistant"}]},
            )
        )
        run_id = run.run_id

    class FakeManager:
        async def add_queue(self, *_args, **_kwargs):
            return asyncio.Queue()

        async def remove_queue(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: FakeManager())
    monkeypatch.setenv("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_SSE_TIMEOUT_SECONDS", "0.1")
    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/threads/{thread_id}/runs/{run_id}/stream")

    assert response.status_code == 200
    assert "event: messages" in response.text
    assert '"content":"hi"' in response.text


@pytest.mark.asyncio
async def test_run_sse_rejects_unsupported_version(pg_runtime) -> None:
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/runs/stream", json={"version": "v4"})
    assert response.status_code == 422
    assert "version='v2' or 'v3'" in response.json()["detail"]


@pytest.mark.asyncio
async def test_run_rejects_missing_checkpoint_and_invalid_interrupts(pg_runtime) -> None:
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assistant = await client.post(
            "/assistants",
            json={"graph_id": "assistant", "name": "checkpoint-validation"},
        )
        thread = await client.post("/threads", json={})
        path = f"/threads/{thread.json()['thread_id']}/runs"
        missing = await client.post(
            path,
            json={
                "assistant_id": assistant.json()["assistant_id"],
                "input": {},
                "checkpoint_id": "missing-checkpoint",
            },
        )
        invalid_interrupt = await client.post(
            path,
            json={
                "assistant_id": assistant.json()["assistant_id"],
                "input": {},
                "interrupt_before": "model",
            },
        )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "checkpoint not found"
    assert invalid_interrupt.status_code == 422


@pytest.mark.asyncio
async def test_protocol_commands_and_thread_event_stream(pg_runtime, monkeypatch) -> None:
    import asyncio
    from uuid import UUID

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow, RuntimeEventRow, ThreadRow
    from langhost.server import create_app

    assistant_id = uuid4()
    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="assistant",
                name="default",
                config={},
                context={},
                metadata_={},
                version=1,
            )
        )

    class FakeManager:
        async def add_thread_stream(self, *_args, **_kwargs):
            return asyncio.Queue()

        async def remove_thread_stream(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: FakeManager())
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_TIMEOUT_SECONDS", "0.1")
    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        command = await client.post(
            f"/threads/{thread_id}/commands",
            json={
                "id": 7,
                "method": "run.start",
                "params": {"assistant_id": str(assistant_id), "input": {"value": 1}},
            },
        )
        assert command.status_code == 200, command.text
        run_id = UUID(command.json()["result"]["run_id"])
        async with connect() as conn:
            thread = await conn.session.get(ThreadRow, thread_id)
            assert thread is not None
            thread.interrupts = {"interrupt-1": {"id": "interrupt-1", "value": {"ok": True}}}
            thread.event_seq = 1
            conn.session.add(
                RuntimeEventRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    sequence=1,
                    topic="values",
                    namespace=[],
                    payload={"event": "values", "data": {"value": 2}, "namespace": []},
                )
            )
        resume = await client.post(
            f"/threads/{thread_id}/commands",
            json={
                "id": 8,
                "method": "input.respond",
                "params": {"interrupt_id": "interrupt-1", "response": "yes"},
            },
        )
        assert resume.status_code == 200
        async with connect() as conn:
            thread = await conn.session.get(ThreadRow, thread_id)
            assert thread is not None
            thread.interrupts = {}
        duplicate_resume = await client.post(
            f"/threads/{thread_id}/commands",
            json={
                "id": 9,
                "method": "input.respond",
                "params": {"interrupt_id": "interrupt-1", "response": "yes"},
            },
        )
        assert duplicate_resume.status_code == 200
        assert duplicate_resume.json()["result"] == resume.json()["result"]
        events = await client.post(
            f"/threads/{thread_id}/stream/events",
            json={"channels": ["values"], "since": 0},
        )

    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert '"method":"values"' in events.text
    assert '"seq":1' in events.text


@pytest.mark.asyncio
async def test_official_python_sdk_core_resource_surface(pg_runtime) -> None:
    from langgraph_sdk._async.client import LangGraphClient

    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with LangGraphClient(
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    ) as client:
        assistant = await client.assistants.create("assistant", name="sdk")
        assert assistant["graph_id"] == "assistant"
        assert await client.assistants.count(graph_id="assistant") == 1
        assert (await client.assistants.search(graph_id="assistant"))[0][
            "assistant_id"
        ] == assistant["assistant_id"]

        thread = await client.threads.create(graph_id="assistant", metadata={"suite": "sdk"})
        assert await client.threads.count(metadata={"suite": "sdk"}) == 1
        found = await client.threads.search(metadata={"suite": "sdk"})
        assert found[0]["thread_id"] == thread["thread_id"]
        updated = await client.threads.update(thread["thread_id"], metadata={"updated": True})
        assert updated["metadata"]["suite"] == "sdk"
        assert updated["metadata"]["updated"] is True

        run = await client.runs.create(
            thread["thread_id"],
            assistant["assistant_id"],
            input={"value": 1},
        )
        assert run["status"] == "pending"
        assert (await client.runs.get(thread["thread_id"], run["run_id"]))["run_id"] == run[
            "run_id"
        ]
        await client.runs.cancel_many(thread_id=thread["thread_id"], run_ids=[run["run_id"]])
        assert (await client.runs.get(thread["thread_id"], run["run_id"]))[
            "status"
        ] == "interrupted"

        cron = await client.crons.create(
            assistant["assistant_id"], schedule="* * * * *", input={"value": 1}
        )
        assert await client.crons.count(assistant_id=assistant["assistant_id"]) == 1
        assert (await client.crons.search(assistant_id=assistant["assistant_id"]))[0][
            "cron_id"
        ] == cron["cron_id"]


def test_resume_command_preserves_official_command_fields() -> None:
    from langgraph_runtime_pg.graph_executor import resume_command

    command = resume_command(
        {
            "resume": {"approved": True},
            "update": {"audit": "approved"},
            "goto": "next_node",
            "graph": "__parent__",
        }
    )
    assert command is not None
    assert command.resume == {"approved": True}
    assert command.update == {"audit": "approved"}
    assert command.goto == "next_node"
    assert command.graph == "__parent__"


@pytest.mark.asyncio
async def test_protocol_errors_use_official_envelope(pg_runtime) -> None:
    from langhost.server import create_app

    app = create_app({"graphs": {}})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/threads/{uuid4()}/commands",
            json={"id": 1, "method": "not.implemented", "params": {}},
        )

    assert response.status_code == 200
    assert response.json()["type"] == "error"
    assert response.json()["error"] == "unknown_command"
    assert response.json()["meta"] == {}


@pytest.mark.asyncio
async def test_idempotency_key_is_race_safe_across_sessions(pg_runtime) -> None:
    from langgraph_runtime_pg.database import connect, get_session_factory
    from langgraph_runtime_pg.models import AssistantRow
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="race-safe",
                name="race-safe",
                config={},
                context={},
                metadata_={},
            )
        )

    async def create_once() -> UUID:
        async with get_session_factory()() as session, session.begin():
            row = await RunRepository().create(
                session,
                assistant_id=assistant_id,
                thread_id=None,
                kwargs={"input": {"value": 1}},
                metadata={},
                tenant_id="tenant-race",
                project_id="project-race",
                idempotency_key="same-resume-key",
            )
            return row.run_id

    run_ids = await asyncio.gather(create_once(), create_once())
    assert run_ids[0] == run_ids[1]
