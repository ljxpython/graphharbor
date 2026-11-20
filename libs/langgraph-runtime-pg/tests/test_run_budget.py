from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from asyncpg import PostgresConnectionError
from psycopg import OperationalError
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from langgraph_runtime_pg.database import connect, run_to_dict
from langgraph_runtime_pg.models import (
    AssistantRow,
    RunLeaseRow,
    RunRow,
    RuntimeEventRow,
    ThreadRow,
)
from langgraph_runtime_pg.production_worker import (
    ProductionWorker,
    _drain_grace_seconds,
    _is_infrastructure_error,
    _run_timeout_seconds,
)
from langgraph_runtime_pg.retry import RetryableException
from langgraph_runtime_pg.run_store import RunOwnershipError, RunRepository

KEY = "__graphharbor_run_budget"


def test_infrastructure_error_classification():
    for exc in (RetryableException("retry"), OperationalError("lost"), PostgresConnectionError()):
        assert _is_infrastructure_error(exc)
        assert _is_infrastructure_error(DBAPIError(None, None, exc))
    assert _is_infrastructure_error(DBAPIError(None, None, None, connection_invalidated=True))
    for exc in (TimeoutError(), ConnectionError(), OSError()):
        assert not _is_infrastructure_error(exc)
        assert not _is_infrastructure_error(DBAPIError(None, None, exc))
    assert not _is_infrastructure_error(DBAPIError(None, None, None))


async def create_run():
    assistant_id, thread_id = uuid4(), uuid4()
    config = {"configurable": {KEY: {"forged": True}}}
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="budget",
                name="budget",
                config=config,
                context={},
                metadata_={},
            )
        )
        conn.session.add(
            ThreadRow(
                thread_id=thread_id, status="idle", config=config, metadata_={}, interrupts={}
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=thread_id,
            kwargs={"input": {}, "config": config, KEY: {"forged": True}},
            metadata={},
        )
        assert KEY not in run.kwargs
        return run.run_id


async def assert_timeout(run_id):
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row.status == row.reason == "timeout"
        assert await conn.session.get(RunLeaseRow, run_id) is None
        count = await conn.session.scalar(
            select(func.count())
            .select_from(RuntimeEventRow)
            .where(RuntimeEventRow.run_id == run_id, RuntimeEventRow.terminal.is_(True))
        )
        assert count == 1
        assert KEY not in str(run_to_dict(row))
        return row.kwargs[KEY]


async def test_claim_refreshes_attempt_budget_across_requeue_and_reaper(pg_runtime):
    run_id = await create_run()
    repository = RunRepository(run_timeout_seconds=2)
    async with connect() as conn:
        first = await repository.claim_next(conn.session, "old")
        budget = first.kwargs[KEY]
        assert first.run_id == run_id
        assert (
            datetime.fromisoformat(budget["deadline_at"])
            - datetime.fromisoformat(budget["started_at"])
        ).total_seconds() == 2
        await repository.requeue_for_shutdown(conn.session, run_id, "old", drained=True)
    replacement = RunRepository(run_timeout_seconds=600)
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        row.next_attempt_at = None
        await conn.session.flush()
        claimed = await replacement.claim_next(conn.session, "new")
        second_budget = claimed.kwargs[KEY]
        assert second_budget["started_at"] > budget["started_at"]
        assert second_budget["timeout_seconds"] == 600
        assert claimed.retry_count == 2
        assert await replacement.attempt_count(conn.session, claimed) == 1
        claimed.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        claimed.kwargs = {
            **claimed.kwargs,
            KEY: {**second_budget, "deadline_at": datetime.now(UTC).isoformat()},
        }
        await conn.session.flush()
        await replacement.requeue_expired(conn.session)
        assert claimed.status == "pending"
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        row.next_attempt_at = None
        await conn.session.flush()
        claimed = await replacement.claim_next(conn.session, "third")
        assert claimed.kwargs[KEY]["started_at"] > second_budget["started_at"]
        assert claimed.retry_count == 3
        with pytest.raises(RunOwnershipError):
            await repository.finish(conn.session, run_id, "old", "timeout", reason="timeout")


async def test_retry_refreshes_budget_and_hides_factory_budget(pg_runtime, monkeypatch):
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("GRAPHHARBOR_RETRY_BASE_SECONDS", "0.1")
    run_id = await create_run()
    captured = []

    @asynccontextmanager
    async def open_graph(_graph_id, config):
        snapshot = config["configurable"][KEY]
        assert snapshot["run_id"] == str(run_id)
        assert "forged" not in snapshot
        captured.append(snapshot)
        yield object()

    async def fail_graph(_graph, _input, *, config, **kwargs):
        assert KEY not in str(config)
        if len(captured) == 1:
            raise RetryableException("database retry")
        return SimpleNamespace(value={"ok": True})

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", fail_graph)
    first = ProductionWorker(SimpleNamespace(open=open_graph), owner="first")
    assert await first.run_once()
    expired_deadline = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        row.kwargs = {**row.kwargs, KEY: {**row.kwargs[KEY], "deadline_at": expired_deadline}}
        row.next_attempt_at = None
        await conn.session.flush()
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "600")
    assert await ProductionWorker(SimpleNamespace(open=open_graph), owner="restarted").run_once()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row.status == "success"
        assert row.kwargs[KEY]["timeout_seconds"] == 600
        assert row.kwargs[KEY]["started_at"] > expired_deadline
        assert KEY not in str(run_to_dict(row))
    assert len(captured) == 2
    assert captured[1]["deadline_monotonic"] > captured[0]["deadline_monotonic"]


@pytest.mark.parametrize("selected_checkpoint", [False, True])
async def test_database_retry_resumes_current_run_checkpoint(
    pg_runtime, monkeypatch, selected_checkpoint
):
    from langgraph.graph import END, START, StateGraph

    from langgraph_runtime_pg.checkpoint import get_checkpointer, setup_checkpointer

    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "60")
    await setup_checkpointer()
    run_id = await create_run()
    calls = []

    async def first(state):
        calls.append("first")
        return {"value": 1}

    async def second(state):
        calls.append("second")
        if calls.count("second") == 1:
            raise RetryableException("database disconnected")
        return {"value": state["value"] + 1}

    builder = StateGraph(dict)
    builder.add_node("first", first)
    builder.add_node("second", second)
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)

    if selected_checkpoint:
        graph = builder.compile(checkpointer=get_checkpointer())
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
            thread_id = str(row.thread_id)
        checkpoint = await graph.aupdate_state(
            {"configurable": {"thread_id": thread_id}}, {"value": 0}, as_node=START
        )
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
            row.kwargs = {
                **row.kwargs,
                "checkpoint_id": checkpoint["configurable"]["checkpoint_id"],
            }
            await conn.session.flush()

    @asynccontextmanager
    async def open_graph(_graph_id, _config):
        yield builder.compile(checkpointer=get_checkpointer())

    registry = SimpleNamespace(open=open_graph, attach_checkpointer=lambda _: None)
    worker = ProductionWorker(registry, owner="database-retry")
    assert await worker.run_once()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row.status == "pending"
        row.next_attempt_at = None
        await conn.session.flush()
    assert await worker.run_once()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row.status == "success"
    assert calls == ["first", "second", "second"]


@pytest.mark.parametrize("failure", ["factory", "execution", "drain"])
async def test_factory_and_shutdown_share_hard_deadline(pg_runtime, monkeypatch, failure):
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS", "10")
    run_id = await create_run()
    started, cleaned = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def open_graph(_graph_id, _config):
        try:
            if failure == "factory":
                started.set()
                await asyncio.sleep(30)
            yield object()
        finally:
            cleaned.set()

    async def slow_graph(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", slow_graph)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="bounded")
    begin = time.monotonic()
    task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(started.wait(), 3)
    if failure == "drain":
        worker.stop_event.set()
    assert await asyncio.wait_for(task, 4)
    assert time.monotonic() - begin < 5
    assert cleaned.is_set()
    await assert_timeout(run_id)


@pytest.mark.parametrize("exception", [TimeoutError, ConnectionError, OSError])
async def test_user_errors_do_not_retry_whole_graph(pg_runtime, monkeypatch, exception):
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("GRAPHHARBOR_RETRY_BASE_SECONDS", "0.1")
    run_id = await create_run()
    calls = 0

    @asynccontextmanager
    async def open_graph(_graph_id, _config):
        yield object()

    async def timeout_model(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise exception("provider failure")

    monkeypatch.setattr("langgraph_runtime_pg.production_worker.invoke_graph", timeout_model)
    worker = ProductionWorker(SimpleNamespace(open=open_graph), owner="retry")
    assert await worker.run_once()
    assert not await worker.run_once()
    async with connect() as conn:
        row = await conn.session.get(RunRow, run_id)
        assert row.status == "error" and row.reason == "business_error"
        assert calls == 1
        assert await conn.session.get(RunLeaseRow, run_id) is None


@pytest.mark.parametrize("max_attempts", [2, 5])
async def test_drained_handoffs_refund_attempts_without_reusing_lease_generation(
    pg_runtime, monkeypatch, max_attempts
):
    monkeypatch.setenv("BG_JOB_MAX_RETRIES", str(max_attempts))
    run_id = await create_run()
    repository = RunRepository(run_timeout_seconds=3)
    for generation in range(1, 7):
        async with connect() as conn:
            row = await repository.claim_next(conn.session, f"worker-{generation}")
            assert row.run_id == run_id
            assert row.retry_count == generation
            assert (await conn.session.get(RunLeaseRow, run_id)).generation == generation
            assert await repository.attempt_count(conn.session, row) == 1
            await repository.requeue_for_shutdown(
                conn.session, run_id, f"worker-{generation}", drained=True
            )
            assert await repository.attempt_count(conn.session, row) == 0
    for attempt in range(1, max_attempts + 1):
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
            row.next_attempt_at = None
            await conn.session.flush()
            assert await repository.claim_next(conn.session, "fault-worker") is not None
            assert row.retry_count == 6 + attempt
            with pytest.raises(RunOwnershipError):
                await repository.finish(
                    conn.session, run_id, "worker-1", "timeout", reason="timeout"
                )
            await repository.fail(
                conn.session,
                run_id,
                "fault-worker",
                infrastructure=True,
                reason="infrastructure_error",
            )
            assert row.status == ("pending" if attempt < max_attempts else "error")
            assert await repository.attempt_count(conn.session, row) == attempt


def test_official_worker_configuration_and_legacy_aliases(monkeypatch):
    for name in (
        "GRAPHHARBOR_RUN_TIMEOUT_SECONDS",
        "BG_JOB_TIMEOUT_SECS",
        "GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS",
        "BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS",
        "BG_JOB_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    assert _run_timeout_seconds() == 86400
    assert _drain_grace_seconds() == 180
    assert RunRepository().max_attempts == 3
    monkeypatch.setenv("BG_JOB_TIMEOUT_SECS", "300")
    monkeypatch.setenv("BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS", "90")
    monkeypatch.setenv("BG_JOB_MAX_RETRIES", "5")
    assert _run_timeout_seconds() == 300
    assert _drain_grace_seconds() == 90
    assert RunRepository().max_attempts == 5
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS", "30")
    assert _run_timeout_seconds() == 120
    assert _drain_grace_seconds() == 30
    monkeypatch.setenv("GRAPHHARBOR_RUN_TIMEOUT_SECONDS", "nan")
    with pytest.raises(ValueError):
        _run_timeout_seconds()
    monkeypatch.setenv("GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS", "3601")
    with pytest.raises(ValueError):
        _drain_grace_seconds()
    monkeypatch.setenv("BG_JOB_MAX_RETRIES", "0")
    with pytest.raises(ValueError):
        RunRepository()
