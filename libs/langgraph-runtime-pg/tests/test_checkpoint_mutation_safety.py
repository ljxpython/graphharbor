from __future__ import annotations

import asyncio
from contextlib import aclosing, asynccontextmanager
from typing import TypedDict
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text


class State(TypedDict):
    value: int


def graph_fixture(saver, entered=None, release=None):
    async def increment(state):
        if entered is not None:
            entered.set()
            await release.wait()
        return {"value": state["value"] + 1}

    builder = StateGraph(State)
    builder.add_node("increment", increment)
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile(checkpointer=saver)


async def snapshot(thread_id):
    from langgraph_runtime_pg.database import connect

    async with connect() as conn:
        return {
            table: list(
                (
                    await conn.session.execute(
                        text(
                            f"SELECT to_jsonb(t) FROM {table} t WHERE thread_id=:id ORDER BY to_jsonb(t)::text"
                        ),
                        {"id": str(thread_id)},
                    )
                ).scalars()
            )
            for table in ("checkpoints", "checkpoint_writes")
        }


async def setup_client():
    from langhost.server import create_app

    client = AsyncClient(
        transport=ASGITransport(app=create_app({"graphs": {}})), base_url="http://test"
    )
    assistant = (await client.post("/assistants", json={"graph_id": "test"})).json()
    thread = (await client.post("/threads", json={})).json()
    return client, assistant["assistant_id"], thread["thread_id"]


async def execute_run(client, assistant_id, thread_id, graph, value):
    from langgraph_runtime_pg.checkpoint_mutations import checkpoint_writer
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import ThreadRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.run_store import RunRepository

    response = await client.post(
        f"/threads/{thread_id}/runs", json={"assistant_id": assistant_id, "input": {"value": value}}
    )
    assert response.status_code == 200
    repository = RunRepository()
    async with connect() as conn:
        run = await repository.claim_next(conn.session, "test-owner")
    token = checkpoint_writer.set((str(run.run_id), "test-owner", run.retry_count))
    try:
        result = await graph.ainvoke({"value": value}, {"configurable": {"thread_id": thread_id}})
    finally:
        checkpoint_writer.reset(token)
    async with connect() as conn:
        await repository.finish(
            conn.session, run.run_id, "test-owner", RunStatus.SUCCESS, reason=RunReason.COMPLETED
        )
        thread = await conn.session.get(ThreadRow, UUID(thread_id))
        thread.values_ = result
        thread.status = "idle"
    return run


async def test_rollback_restores_history_and_rejects_late_writer(pg_runtime):
    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.checkpoint_mutations import CheckpointConflict, checkpoint_writer

    client, assistant, tid = await setup_client()
    async with aclosing(client):
        graph = graph_fixture(get_checkpointer())
        await execute_run(client, assistant, tid, graph, 1)
        before = await snapshot(tid)
        run = await execute_run(client, assistant, tid, graph, 10)
        response = await client.post(
            f"/threads/{tid}/runs/{run.run_id}/cancel?action=rollback&wait=true"
        )
        assert response.status_code == 200, response.text
        assert await snapshot(tid) == before
        assert (await client.get(f"/threads/{tid}")).json()["values"] == {"value": 2}
        token = checkpoint_writer.set((str(run.run_id), "test-owner", run.retry_count))
        try:
            with pytest.raises(CheckpointConflict):
                await graph.ainvoke({"value": 500}, {"configurable": {"thread_id": tid}})
        finally:
            checkpoint_writer.reset(token)
        assert await snapshot(tid) == before
        await execute_run(client, assistant, tid, graph, 2)
        assert (await client.get(f"/threads/{tid}")).json()["values"] == {"value": 3}


async def test_root_run_checkpoint_uses_run_id_for_fencing(pg_runtime):
    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.checkpoint_mutations import checkpoint_writer
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import AssistantRow
    from langgraph_runtime_pg.run_store import RunRepository

    assistant_id = uuid4()
    async with connect() as conn:
        conn.session.add(
            AssistantRow(
                assistant_id=assistant_id,
                graph_id="root",
                name="root",
                config={},
                context={},
                metadata_={},
            )
        )
        run = await RunRepository().create(
            conn.session,
            assistant_id=assistant_id,
            thread_id=None,
            kwargs={"input": {"value": 1}},
            metadata={},
        )
        claimed = await RunRepository().claim_next(conn.session, "root-worker")
        assert claimed is not None and claimed.run_id == run.run_id

    graph = graph_fixture(get_checkpointer())
    token = checkpoint_writer.set((str(run.run_id), "root-worker", claimed.retry_count))
    try:
        result = await graph.ainvoke({"value": 1}, {"configurable": {"thread_id": str(run.run_id)}})
    finally:
        checkpoint_writer.reset(token)
    assert result == {"value": 2}


async def test_prune_authorized_ids_and_input_validation(pg_runtime):
    from langgraph_sdk import Auth

    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import ThreadRow
    from langhost.server import create_app

    ids = [uuid4(), uuid4()]
    async with connect() as conn:
        for index, tid in enumerate(ids):
            conn.session.add(ThreadRow(thread_id=tid, metadata_={"owner": str(index)}))
    graph = graph_fixture(get_checkpointer())
    for tid in ids:
        await graph.ainvoke({"value": 1}, {"configurable": {"thread_id": str(tid)}})
    other_before = await snapshot(ids[1])
    own_before = await snapshot(ids[0])

    auth = Auth()

    @auth.authenticate
    async def authenticate():
        return {"identity": "0"}

    @auth.on.threads
    async def authorize(ctx, value):
        del value
        return {"owner": ctx.user.identity}

    app = create_app({"graphs": {}})
    # Use the same SDK Auth callback dispatch as production resource handlers.
    app.user_middleware[0].kwargs["auth_handler"] = auth
    app.state.auth_handler = auth
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for body in (
            [],
            {},
            {"thread_ids": "bad"},
            {"thread_ids": ["bad"]},
            {"thread_ids": [], "strategy": "typo"},
        ):
            assert (await client.post("/threads/prune", json=body)).status_code == 422
        response = await client.post(
            "/threads/prune",
            json={
                "thread_ids": [str(ids[0]), str(ids[1]), str(ids[0]), str(uuid4())],
                "strategy": "keep_latest",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"pruned_count": 1}
    assert await snapshot(ids[1]) == other_before
    assert len((await snapshot(ids[0]))["checkpoints"]) < len(own_before["checkpoints"])
    assert (await graph.aget_state({"configurable": {"thread_id": str(ids[0])}})).values == {
        "value": 2
    }


async def test_running_rollback_waits_for_worker_and_restores_baseline(pg_runtime, monkeypatch):
    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.production_worker import ProductionWorker

    monkeypatch.setenv("LG_BG_JOB_HEARTBEAT", "1")
    client, assistant, tid = await setup_client()
    async with aclosing(client):
        await execute_run(client, assistant, tid, graph_fixture(get_checkpointer()), 1)
        before = await snapshot(tid)
        entered, release = asyncio.Event(), asyncio.Event()
        graph = graph_fixture(get_checkpointer(), entered, release)

        class Registry:
            @asynccontextmanager
            async def open(self, *args):
                yield graph

        response = await client.post(
            f"/threads/{tid}/runs", json={"assistant_id": assistant, "input": {"value": 10}}
        )
        run_id = response.json()["run_id"]
        worker = ProductionWorker(Registry(), owner="real-worker")
        task = asyncio.create_task(worker.run_once())
        try:
            await asyncio.wait_for(entered.wait(), 10)
            response = await asyncio.wait_for(
                client.post(f"/threads/{tid}/runs/{run_id}/cancel?action=rollback&wait=true"), 15
            )
            assert response.status_code == 200, response.text
            await asyncio.wait_for(task, 5)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert await snapshot(tid) == before
        assert (await client.get(f"/threads/{tid}/runs/{run_id}")).status_code == 404


async def test_pending_rollback_preserves_existing_checkpoints(pg_runtime):
    from langgraph_runtime_pg.checkpoint import get_checkpointer

    client, assistant, tid = await setup_client()
    async with aclosing(client):
        await execute_run(client, assistant, tid, graph_fixture(get_checkpointer()), 1)
        before = await snapshot(tid)
        run = (await client.post(f"/threads/{tid}/runs", json={"assistant_id": assistant})).json()
        response = await client.post(f"/threads/{tid}/runs/{run['run_id']}/cancel?action=rollback")
        assert response.status_code == 200
        assert await snapshot(tid) == before


async def test_delta_prune_and_subgraph_history(pg_runtime):
    from typing import Annotated

    from langgraph.channels import DeltaChannel

    from langgraph_runtime_pg.checkpoint import get_checkpointer

    def reducer(state, writes):
        return (state or []) + [item for batch in writes for item in batch]

    # Resolve the local channel annotation explicitly (future annotations are strings).
    DeltaState = TypedDict(  # noqa: UP013
        "DeltaState", {"items": Annotated[list[int], DeltaChannel(reducer, snapshot_frequency=3)]}
    )
    child = StateGraph(DeltaState)
    child.add_node("append", lambda state: {"items": [len(state["items"])]})
    child.add_edge(START, "append")
    child.add_edge("append", END)
    parent = StateGraph(DeltaState)
    parent.add_node("child", child.compile())
    parent.add_edge(START, "child")
    parent.add_edge("child", END)
    graph = parent.compile(checkpointer=get_checkpointer())
    client, _, tid = await setup_client()
    config = {"configurable": {"thread_id": tid}}
    async with aclosing(client):
        for value in range(5):
            await graph.ainvoke({"items": [value]}, config)
        before = (await graph.aget_state(config)).values
        history = await snapshot(tid)
        assert any(c["checkpoint_ns"] for c in history["checkpoints"])
        response = await client.post(
            "/threads/prune", json={"thread_ids": [tid], "strategy": "keep_latest"}
        )
        assert response.status_code == 200, response.text
        assert (await graph.aget_state(config)).values == before
        assert len((await snapshot(tid))["checkpoints"]) < len(history["checkpoints"])
        result = await graph.ainvoke({"items": [99]}, config)
        assert result["items"][: len(before["items"])] == before["items"]


async def test_historical_rollback_conflict_and_batch_reverse_restore(pg_runtime):
    from langgraph_runtime_pg.checkpoint import get_checkpointer

    client, assistant, tid = await setup_client()
    async with aclosing(client):
        graph = graph_fixture(get_checkpointer())
        await execute_run(client, assistant, tid, graph, 1)
        baseline = await snapshot(tid)
        run_b = await execute_run(client, assistant, tid, graph, 2)
        run_c = await execute_run(client, assistant, tid, graph, 3)
        current = await snapshot(tid)
        response = await client.post(f"/threads/{tid}/runs/{run_b.run_id}/cancel?action=rollback")
        assert response.status_code == 409
        assert await snapshot(tid) == current
        response = await client.post(
            "/runs/cancel?action=rollback", json={"run_ids": [str(run_b.run_id), str(run_c.run_id)]}
        )
        assert response.status_code == 200, response.text
        assert await snapshot(tid) == baseline


async def test_rollback_intent_survives_pool_restart_and_blocks_mutations(pg_runtime):
    from datetime import UTC, datetime, timedelta

    from langgraph_runtime_pg.checkpoint import get_checkpointer, reconnect_checkpointer
    from langgraph_runtime_pg.checkpoint_mutations import (
        CheckpointConflict,
        checkpoint_writer,
    )
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import RunRow
    from langgraph_runtime_pg.run_store import RunRepository

    client, assistant, tid = await setup_client()
    async with aclosing(client):
        graph = graph_fixture(get_checkpointer())
        await execute_run(client, assistant, tid, graph, 1)
        baseline = await snapshot(tid)
        await client.post(f"/threads/{tid}/runs", json={"assistant_id": assistant})
        async with connect() as conn:
            run = await RunRepository().claim_next(conn.session, "dead-worker")
        token = checkpoint_writer.set((str(run.run_id), "dead-worker", run.retry_count))
        try:
            await graph.ainvoke({"value": 9}, {"configurable": {"thread_id": tid}})
        finally:
            checkpoint_writer.reset(token)
        assert (
            await client.post(f"/threads/{tid}/runs/{run.run_id}/cancel?action=rollback")
        ).status_code == 200
        current = await snapshot(tid)
        assert (
            await client.post(
                "/threads/prune", json={"thread_ids": [tid], "strategy": "keep_latest"}
            )
        ).status_code == 409
        with pytest.raises(CheckpointConflict):
            await graph.aupdate_state({"configurable": {"thread_id": tid}}, {"value": 99})
        assert await snapshot(tid) == current
        await client.post(f"/threads/{tid}/runs", json={"assistant_id": assistant})
        async with connect() as conn:
            assert await RunRepository().claim_next(conn.session, "other-worker") is None
            row = await conn.session.get(RunRow, run.run_id)
            row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await reconnect_checkpointer()
        # A fresh process has no ContextVar, saver pool or worker state from this run.
        import sys

        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import asyncio; from langgraph_runtime_pg.database import start_pool, stop_pool; "
            "from langgraph_runtime_pg.checkpoint_mutations import complete_rollbacks; "
            'exec("async def main():\\n await start_pool()\\n await complete_rollbacks()\\n await stop_pool()"); '
            "asyncio.run(main())",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 0, (stdout + stderr).decode()
        assert await snapshot(tid) == baseline
        assert (await client.get(f"/threads/{tid}/runs/{run.run_id}")).status_code == 404
        async with connect() as conn:
            assert await RunRepository().claim_next(conn.session, "other-worker") is not None


async def test_restore_failure_is_atomic_and_retryable(pg_runtime, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.checkpoint_mutations import rollback_run
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import RunRow

    client, assistant, tid = await setup_client()
    async with aclosing(client):
        graph = graph_fixture(get_checkpointer())
        await execute_run(client, assistant, tid, graph, 1)
        baseline = await snapshot(tid)
        run = await execute_run(client, assistant, tid, graph, 9)
        current = await snapshot(tid)
        original = AsyncSession.execute

        async def fail_insert(self, statement, *args, **kwargs):
            if str(statement).startswith("INSERT INTO checkpoints SELECT"):
                raise RuntimeError("injected restore failure")
            return await original(self, statement, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", fail_insert)
            with pytest.raises(RuntimeError, match="injected restore failure"):
                async with connect() as conn:
                    row = await conn.session.get(RunRow, run.run_id, with_for_update=True)
                    await rollback_run(conn.session, row)
        assert await snapshot(tid) == current
        assert (await client.get(f"/threads/{tid}/runs/{run.run_id}")).status_code == 200
        assert (
            await client.post(f"/threads/{tid}/runs/{run.run_id}/cancel?action=rollback")
        ).status_code == 200
        assert await snapshot(tid) == baseline


async def test_hitl_prune_preserves_pending_writes_and_resume(pg_runtime):
    from langgraph.types import Command, interrupt

    from langgraph_runtime_pg.checkpoint import get_checkpointer

    def approval(state):
        return {"value": interrupt("approve")}

    builder = StateGraph(State)
    builder.add_node("approval", approval)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=get_checkpointer())
    client, _, tid = await setup_client()
    config = {"configurable": {"thread_id": tid}}
    async with aclosing(client):
        await graph.ainvoke({"value": 1}, config)
        before = await graph.aget_state(config)
        assert before.tasks[0].interrupts
        response = await client.post(
            "/threads/prune", json={"thread_ids": [tid], "strategy": "keep_latest"}
        )
        assert response.status_code == 200
        after = await graph.aget_state(config)
        assert after.tasks == before.tasks
        assert (await graph.ainvoke(Command(resume=42), config))["value"] == 42


async def test_legacy_prune_applies_delete_authorization(pg_runtime, monkeypatch):
    from langgraph_runtime_pg.checkpoint import get_checkpointer
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import ThreadRow
    from langgraph_runtime_pg.ops import Threads

    own, other = uuid4(), uuid4()
    async with connect() as conn:
        conn.session.add_all(
            [
                ThreadRow(thread_id=own, metadata_={"owner": "a"}),
                ThreadRow(thread_id=other, metadata_={"owner": "b"}),
            ]
        )
    graph = graph_fixture(get_checkpointer())
    for tid in [own, other]:
        await graph.ainvoke({"value": 1}, {"configurable": {"thread_id": str(tid)}})
    before = await snapshot(other)
    events = []

    async def authorize(ctx, action, value):
        events.append(action)
        return {"owner": "a"}

    monkeypatch.setattr(Threads, "handle_event", authorize)
    assert await Threads.prune([own, other, own], strategy="keep_latest") == 1
    assert events == ["delete", "delete"]
    assert await snapshot(other) == before
