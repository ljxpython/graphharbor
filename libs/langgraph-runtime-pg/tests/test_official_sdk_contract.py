from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient


def _write_graph_project(root: Path) -> dict[str, dict[str, str]]:
    (root / "graphs.py").write_text(
        "from typing import TypedDict\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "class State(TypedDict):\n"
        "    value: int\n"
        "def increment(state: State):\n"
        "    return {'value': state['value'] + 1}\n"
        "builder = StateGraph(State)\n"
        "builder.add_node('increment', increment)\n"
        "builder.add_edge(START, 'increment')\n"
        "builder.add_edge('increment', END)\n"
        "graph = builder.compile()\n",
        encoding="utf-8",
    )
    return {"assistant": {"path": "graphs.py:graph"}}


@pytest.mark.asyncio
async def test_official_python_sdk_core_surface_is_complete(pg_runtime, tmp_path: Path) -> None:
    from langgraph_sdk._async.client import LangGraphClient

    from langhost.server import create_app

    app = create_app(
        {"graphs": _write_graph_project(tmp_path)},
        base_dir=tmp_path,
    )
    async with LifespanManager(app):  # noqa: SIM117 - keep SDK client scope explicit
        async with LangGraphClient(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        ) as client:
            assistant = await client.assistants.create(
                "assistant",
                name="contract",
                description="contract assistant",
                config={"configurable": {"temperature": 0}},
                context={"tenant": "test"},
                metadata={"suite": "core"},
            )
            assert (await client.assistants.get(assistant["assistant_id"]))["name"] == "contract"
            await client.store.put_item(["sdk", "contract"], "item", {"value": 1})
            item = await client.store.get_item(["sdk", "contract"], "item")
            assert item["value"] == {"value": 1}
            found_items = await client.store.search_items(["sdk"])
            assert found_items["items"][0]["key"] == "item"
            assert await client.store.list_namespaces(prefix=["sdk"]) == {
                "namespaces": [["sdk", "contract"]]
            }
            await client.store.delete_item(["sdk", "contract"], "item")
            assert await client.store.get_item(["sdk", "contract"], "item") is None
            assert await client.assistants.count(graph_id="assistant") == 1
            assert (await client.assistants.search(graph_id="assistant"))[0][
                "assistant_id"
            ] == assistant["assistant_id"]
            object_search = await client.assistants.search(
                graph_id="assistant", response_format="object"
            )
            assert object_search["assistants"][0]["assistant_id"] == assistant["assistant_id"]
            assert await client.assistants.get_versions(assistant["assistant_id"])
            assert (await client.assistants.set_latest(assistant["assistant_id"], 1))[
                "version"
            ] == 1
            assert "nodes" in await client.assistants.get_graph(assistant["assistant_id"])
            schemas = await client.assistants.get_schemas(assistant["assistant_id"])
            assert schemas["graph_id"] == "assistant"
            assert await client.assistants.get_subgraphs(assistant["assistant_id"]) == {}
            updated_assistant = await client.assistants.update(
                assistant["assistant_id"], metadata={"updated": True}
            )
            assert updated_assistant["metadata"]["updated"] is True

            thread = await client.threads.create(graph_id="assistant", metadata={"suite": "core"})
            thread_id = thread["thread_id"]
            assert (await client.threads.get(thread_id))["thread_id"] == thread_id
            assert await client.threads.count(metadata={"suite": "core"}) == 1
            assert (await client.threads.search(metadata={"suite": "core"}))[0][
                "thread_id"
            ] == thread_id
            assert (await client.threads.update(thread_id, metadata={"updated": True}))["metadata"][
                "updated"
            ] is True
            state = await client.threads.get_state(thread_id)
            assert state["values"] == {}
            update_state = await client.threads.update_state(thread_id, {"value": 3})
            assert update_state["checkpoint"]["thread_id"] == thread_id
            assert (await client.threads.get_state(thread_id))["values"]["value"] == 3
            assert await client.threads.get_history(thread_id, limit=5)
            copied = await client.threads.copy(thread_id)
            assert copied["thread_id"] != thread_id
            assert (await client.threads.get_state(copied["thread_id"]))["values"]["value"] == 3

            run = await client.runs.create(
                thread_id,
                assistant["assistant_id"],
                input={"value": 1},
                metadata={"suite": "core"},
            )
            run_id = run["run_id"]
            assert (await client.runs.get(thread_id, run_id))["run_id"] == run_id
            assert (await client.runs.list(thread_id))[0]["run_id"] == run_id
            batch = await client.runs.create_batch(
                [
                    {
                        "assistant_id": assistant["assistant_id"],
                        "thread_id": thread_id,
                        "input": {"value": 2},
                    },
                    {
                        "assistant_id": assistant["assistant_id"],
                        "thread_id": thread_id,
                        "input": {"value": 3},
                    },
                ]
            )
            assert len(batch) == 2
            await client.runs.cancel(thread_id, run_id)
            assert (await client.runs.get(thread_id, run_id))["status"] == "interrupted"
            await client.runs.cancel_many(
                run_ids=[item["run_id"] for item in batch], status="pending"
            )
            for item in batch:
                assert (await client.runs.get(thread_id, item["run_id"]))["status"] == "interrupted"
            await client.runs.delete(thread_id, run_id)

            cron = await client.crons.create(
                assistant["assistant_id"],
                schedule="* * * * *",
                input={"value": 1},
                metadata={"suite": "core"},
            )
            assert await client.crons.count(assistant_id=assistant["assistant_id"]) == 1
            assert (await client.crons.search(assistant_id=assistant["assistant_id"]))[0][
                "cron_id"
            ] == cron["cron_id"]
            updated_cron = await client.crons.update(
                cron["cron_id"], schedule="*/5 * * * *", enabled=False
            )
            assert updated_cron["schedule"] == "*/5 * * * *"
            thread_cron = await client.crons.create_for_thread(
                thread_id, assistant["assistant_id"], schedule="*/10 * * * *"
            )
            await client.crons.delete(thread_cron["cron_id"])
            await client.crons.delete(cron["cron_id"])

            pruned = await client.threads.prune([thread_id, copied["thread_id"]])
            assert pruned["pruned_count"] == 2
            await client.assistants.delete(assistant["assistant_id"])

    # Keep this contract test deterministic if the SDK adds an extra JSON field.
    assert json.dumps(assistant, sort_keys=True)


@pytest.mark.asyncio
async def test_thread_stream_projects_durable_events_and_resumes(pg_runtime, monkeypatch) -> None:
    from starlette.requests import Request

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import RuntimeEventRow, ThreadRow
    from langhost.streaming import thread_stream

    class FakeManager:
        async def add_thread_stream(self, _thread_id: UUID) -> asyncio.Queue:
            return asyncio.Queue()

        async def remove_thread_stream(self, _thread_id: UUID, _queue: asyncio.Queue) -> None:
            return None

    thread_id = uuid4()
    async with connect() as conn:
        conn.session.add(ThreadRow(thread_id=thread_id, metadata_={}, config={}, interrupts={}))
        await conn.session.flush()
        for sequence, value in enumerate((1, 2), start=1):
            conn.session.add(
                RuntimeEventRow(
                    thread_id=thread_id,
                    sequence=sequence,
                    topic="values",
                    namespace=[],
                    payload={"event": "values", "data": {"value": value}},
                )
            )

    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: FakeManager())

    def request(last_event_id: str) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": f"/threads/{thread_id}/stream",
                "query_string": b"stream_modes=run_modes",
                "headers": [(b"last-event-id", last_event_id.encode())],
                "path_params": {"thread_id": str(thread_id)},
            }
        )

    initial = await thread_stream(request("-"))
    assert "event: values\ndata: {\"value\":1}\nid: 1-0" in await anext(initial.body_iterator)
    await initial.body_iterator.aclose()

    resumed = await thread_stream(request("1-0"))
    assert "event: values\ndata: {\"value\":2}\nid: 2-0" in await anext(resumed.body_iterator)
    await resumed.body_iterator.aclose()


@pytest.mark.asyncio
async def test_official_python_sdk_protocol_v2_multi_interrupt_resume(
    pg_runtime, tmp_path: Path, monkeypatch
) -> None:
    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import RuntimeEventRow, ThreadRow
    from langgraph_runtime_pg.protocol import protocol_event
    from langgraph_runtime_pg.redis_stream import Message
    from langhost.server import create_app

    class FakeManager:
        def __init__(self) -> None:
            self.queues: dict[UUID, asyncio.Queue[Message]] = {}

        async def add_thread_stream(self, thread_id: UUID):
            return self.queues.setdefault(thread_id, asyncio.Queue())

        async def remove_thread_stream(self, thread_id: UUID, queue: asyncio.Queue[Message]):
            if self.queues.get(thread_id) is queue:
                self.queues.pop(thread_id, None)

        async def push(self, thread_id: UUID, wire: dict) -> None:
            await self.queues[thread_id].put(
                Message(topic=b"protocol:event", data=json.dumps(wire).encode())
            )

    manager = FakeManager()
    monkeypatch.setattr("langhost.protocol_api.get_stream_manager", lambda: manager)
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_PROTOCOL_TIMEOUT_SECONDS", "0.05")
    app = create_app({"graphs": _write_graph_project(tmp_path)}, base_dir=tmp_path)

    from httpx import ASGITransport, AsyncClient
    from langgraph_sdk._async.client import LangGraphClient

    async with (
        LifespanManager(app),
        LangGraphClient(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        ) as client,
    ):
        assistant = await client.assistants.create("assistant")
        thread = await client.threads.create(graph_id="assistant")
        thread_id = UUID(thread["thread_id"])

        async with client.threads.stream(
            thread_id=str(thread_id), assistant_id=assistant["assistant_id"]
        ) as stream:
            started = await stream.run.start(input={"value": 1})
            run_id = UUID(started["run_id"])
            for _ in range(100):
                if thread_id in manager.queues:
                    break
                await asyncio.sleep(0.001)
            assert thread_id in manager.queues

            async with connect() as conn:
                row = await conn.session.get(ThreadRow, thread_id)
                assert row is not None
                row.status = "interrupted"
                row.interrupts = {
                    "interrupt-1": {
                        "id": "interrupt-1",
                        "value": {"question": "first"},
                    },
                    "interrupt-2": {
                        "id": "interrupt-2",
                        "value": {"question": "second"},
                    },
                }
                events = [
                    {
                        "event": "lifecycle",
                        "status": "running",
                    },
                    {
                        "event": "input.requested",
                        "data": {
                            "interrupt_id": "interrupt-1",
                            "value": {"question": "first"},
                        },
                    },
                    {
                        "event": "input.requested",
                        "data": {
                            "interrupt_id": "interrupt-2",
                            "value": {"question": "second"},
                        },
                    },
                    {
                        "event": "lifecycle",
                        "status": "interrupted",
                        "reason": "hitl_interrupt",
                    },
                ]
                wires = []
                for sequence, event in enumerate(events, start=1):
                    event_row = RuntimeEventRow(
                        run_id=run_id,
                        thread_id=thread_id,
                        sequence=sequence,
                        topic=str(event["event"]),
                        namespace=[],
                        payload=event,
                    )
                    conn.session.add(event_row)
                    await conn.session.flush()
                    wires.append(
                        protocol_event(
                            event_id=str(event_row.event_id),
                            sequence=sequence,
                            run_id=str(run_id),
                            thread_id=str(thread_id),
                            event=event,
                        )
                    )

            for wire in wires:
                await manager.push(thread_id, wire)

            for _ in range(200):
                if len(stream.interrupts) == 2:
                    break
                await asyncio.sleep(0.001)
            assert len(stream.interrupts) == 2, (stream.interrupts, manager.queues)
            assert stream.interrupted is True
            assert {item["interrupt_id"] for item in stream.interrupts} == {
                "interrupt-1",
                "interrupt-2",
            }
            with pytest.raises(RuntimeError, match="ambiguous"):
                await stream.run.respond("yes")

            resumed = await stream.run.respond("yes", interrupt_id="interrupt-1")
            duplicate = await stream.run.respond("yes", interrupt_id="interrupt-1")
            assert resumed["run_id"] == duplicate["run_id"]
            assert resumed["run_id"] != started["run_id"]

        async with connect() as conn:
            persisted = await conn.session.get(ThreadRow, thread_id)
            assert persisted is not None
            assert set(persisted.interrupts) == {"interrupt-2"}


@pytest.mark.asyncio
async def test_official_python_sdk_runs_stream_v2_and_replay(
    pg_runtime, tmp_path: Path, monkeypatch
) -> None:

    from langgraph_runtime_pg.database import connect
    from langgraph_runtime_pg.models import RunRow, RuntimeEventRow
    from langgraph_runtime_pg.protocol import RunReason, RunStatus
    from langgraph_runtime_pg.redis_stream import Message
    from langhost.server import create_app

    class FakeManager:
        def __init__(self) -> None:
            self.queues: dict[UUID, asyncio.Queue[Message]] = {}

        async def add_queue(self, run_id: UUID, *_args, **_kwargs):
            return self.queues.setdefault(run_id, asyncio.Queue())

        async def remove_queue(self, run_id: UUID, _thread_id: UUID | None, queue):
            if self.queues.get(run_id) is queue:
                self.queues.pop(run_id, None)

        async def push(self, run_id: UUID, envelope: dict) -> None:
            await self.queues[run_id].put(
                Message(topic=b"event", data=json.dumps(envelope).encode())
            )

    manager = FakeManager()
    monkeypatch.setattr("langhost.streaming.get_stream_manager", lambda: manager)
    monkeypatch.setenv("GRAPHHARBOR_SSE_HEARTBEAT_SECONDS", "0.01")
    monkeypatch.setenv("GRAPHHARBOR_SSE_TIMEOUT_SECONDS", "0.05")
    app = create_app({"graphs": _write_graph_project(tmp_path)}, base_dir=tmp_path)

    from httpx import ASGITransport, AsyncClient
    from langgraph_sdk._async.client import LangGraphClient

    async with (
        LifespanManager(app),
        LangGraphClient(
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        ) as client,
    ):
        assistant = await client.assistants.create("assistant")
        thread = await client.threads.create(graph_id="assistant")
        thread_id = UUID(thread["thread_id"])
        run = await client.runs.create(
            thread["thread_id"], assistant["assistant_id"], input={"value": 1}
        )
        run_id = UUID(run["run_id"])

        events = [
            {"event": "values", "data": {"value": 2}, "namespace": []},
            {
                "event": "values",
                "data": {"value": 3},
                "namespace": ["child:run-1"],
            },
            {
                "event": "lifecycle",
                "status": RunStatus.SUCCESS.value,
                "reason": RunReason.COMPLETED.value,
            },
        ]
        async with connect() as conn:
            row = await conn.session.get(RunRow, run_id)
            assert row is not None
            row.status = RunStatus.SUCCESS.value
            row.reason = RunReason.COMPLETED.value
            row.kwargs = {
                **row.kwargs,
                "stream_mode": ["values", "updates"],
                "stream_subgraphs": True,
            }
            for sequence, event in enumerate(events, start=1):
                event_row = RuntimeEventRow(
                    run_id=run_id,
                    thread_id=thread_id,
                    sequence=sequence,
                    topic=str(event["event"]),
                    namespace=list(event.get("namespace") or []),
                    payload=event,
                )
                conn.session.add(event_row)
                await conn.session.flush()
        from starlette.responses import JSONResponse

        from langhost.core_api import _run

        async def reuse_run(request, *, thread_value=None, payload=None):
            del request, thread_value, payload
            async with connect() as conn:
                persisted = await conn.session.get(RunRow, run_id)
            assert persisted is not None
            return JSONResponse(_run(persisted), status_code=201)

        monkeypatch.setattr("langhost.core_api.runs_create", reuse_run)
        created_metadata = []
        chunks = []
        async for chunk in client.runs.stream(
            thread["thread_id"],
            assistant["assistant_id"],
            input={"value": 1},
            stream_mode=["values", "updates"],
            stream_subgraphs=True,
            version="v2",
            on_run_created=created_metadata.append,
        ):
            chunks.append(chunk)

        assert created_metadata and created_metadata[0]["run_id"] == str(run_id)
        assert {chunk["type"] for chunk in chunks} >= {"metadata", "values"}
        assert any(chunk["ns"] == [] and chunk["data"] == {"value": 2} for chunk in chunks)
        assert any(
            chunk["ns"] == ["child:run-1"] and chunk["data"] == {"value": 3} for chunk in chunks
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as raw:
            replay = await raw.get(
                f"/threads/{thread_id}/runs/{run_id}/stream",
                headers={"last-event-id": "1"},
            )
        assert replay.status_code == 200
        assert "id: 2" in replay.text
        assert "id: 1" not in replay.text
