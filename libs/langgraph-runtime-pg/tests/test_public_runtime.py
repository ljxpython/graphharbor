from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
from langgraph.graph import END, START, StateGraph


class _State(TypedDict):
    value: int


def _graph():
    builder = StateGraph(_State)
    builder.add_node("increment", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "increment")
    builder.add_edge("increment", END)
    return builder.compile()


def test_graph_registry_loads_standard_config(tmp_path: Path) -> None:
    module = tmp_path / "graphs.py"
    module.write_text(
        "from typing import TypedDict\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "class State(TypedDict):\n"
        "    value: int\n"
        "def _graph():\n"
        "    b = StateGraph(State)\n"
        "    b.add_node('increment', lambda s: {'value': s['value'] + 1})\n"
        "    b.add_edge(START, 'increment')\n"
        "    b.add_edge('increment', END)\n"
        "    return b.compile()\n",
        encoding="utf-8",
    )
    config = tmp_path / "langgraph.json"
    config.write_text(json.dumps({"graphs": {"assistant": "graphs.py:_graph"}}), encoding="utf-8")

    from langgraph_runtime_pg.graph_registry import GraphRegistry

    registry = GraphRegistry.from_path(config)
    assert registry.ids() == ("assistant",)


def test_thread_config_preserves_run_fields() -> None:
    from langgraph_runtime_pg.graph_executor import thread_config

    config = thread_config(
        "server-thread",
        configurable={"thread_id": "client-thread", "model_id": "model-a"},
        metadata={"request": "run-1"},
        tags=("runtime", "test"),
        context={"temperature": 0.2},
    )

    assert config["configurable"]["thread_id"] == "server-thread"
    assert config["configurable"]["model_id"] == "model-a"
    assert config["metadata"] == {"request": "run-1"}
    assert config["tags"] == ["runtime", "test"]
    assert cast(dict[str, Any], config)["context"] == {"temperature": 0.2}


def test_thread_metadata_is_exposed_under_a_server_owned_key() -> None:
    from langgraph_runtime_pg.thread_config import attach_thread_metadata

    metadata = {"__graphharbor_thread_metadata": {"forged": True}}
    attach_thread_metadata(metadata, {"resource_id": "sandbox-1"})
    assert metadata["__graphharbor_thread_metadata"] == {"resource_id": "sandbox-1"}


def test_graph_registry_resolves_project_root_relative_paths(tmp_path: Path) -> None:
    package = tmp_path / "runtime_service"
    package.mkdir()
    module = tmp_path / "graphs.py"
    module.write_text(
        "from langgraph.graph import END, START, StateGraph\n"
        "from typing import TypedDict\n"
        "class State(TypedDict):\n"
        "    value: int\n"
        "def _graph():\n"
        "    b = StateGraph(State)\n"
        "    b.add_node('increment', lambda s: {'value': s['value'] + 1})\n"
        "    b.add_edge(START, 'increment')\n"
        "    b.add_edge('increment', END)\n"
        "    return b.compile()\n",
        encoding="utf-8",
    )
    config = package / "langgraph.json"
    config.write_text(json.dumps({"graphs": {"assistant": "./graphs.py:_graph"}}), encoding="utf-8")

    from langgraph_runtime_pg.graph_registry import GraphRegistry

    assert GraphRegistry.from_path(config).ids() == ("assistant",)


def test_graph_registry_loads_src_layout_project(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "runtime_service"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("VALUE = 2\n", encoding="utf-8")
    (source_root / "graphs.py").write_text(
        "from runtime_service import VALUE\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "from typing import TypedDict\n"
        "class State(TypedDict):\n"
        "    value: int\n"
        "def get_agent():\n"
        "    b = StateGraph(State)\n"
        "    b.add_node('increment', lambda s: {'value': s['value'] + VALUE})\n"
        "    b.add_edge(START, 'increment')\n"
        "    b.add_edge('increment', END)\n"
        "    return b.compile()\n",
        encoding="utf-8",
    )
    config = tmp_path / "langgraph.json"
    config.write_text(
        json.dumps({"graphs": {"assistant": "./src/runtime_service/graphs.py:get_agent"}}),
        encoding="utf-8",
    )

    from langgraph_runtime_pg.graph_registry import GraphRegistry

    registry = GraphRegistry.from_path(config)
    assert registry.ids() == ("assistant",)


def test_graph_registry_rejects_symlink_escape(tmp_path: Path) -> None:
    external = tmp_path.parent / "escaped_graphs.py"
    external.write_text(
        "from langgraph.graph import END, START, StateGraph\n"
        "def _graph():\n"
        "    builder = StateGraph(dict)\n"
        "    builder.add_node('done', lambda state: state)\n"
        "    builder.add_edge(START, 'done')\n"
        "    builder.add_edge('done', END)\n"
        "    return builder.compile()\n",
        encoding="utf-8",
    )
    package = tmp_path / "runtime_service"
    package.mkdir()
    (package / "graphs.py").symlink_to(external)
    config = package / "langgraph.json"
    config.write_text(json.dumps({"graphs": {"assistant": "./graphs.py:_graph"}}), encoding="utf-8")

    from langgraph_runtime_pg.graph_registry import GraphRegistry

    with pytest.raises(ValueError, match="escapes base directory"):
        GraphRegistry.from_path(config)


@pytest.mark.asyncio
async def test_graph_registry_resolves_async_per_run_factory_and_closes_context(
    tmp_path: Path,
) -> None:
    module = tmp_path / "graphs.py"
    module.write_text(
        "from contextlib import asynccontextmanager\n"
        "from typing import TypedDict\n"
        "from langgraph.graph import END, START, StateGraph\n"
        "class State(TypedDict):\n"
        "    marker: str\n"
        "@asynccontextmanager\n"
        "async def get_agent(config):\n"
        "    marker = config['metadata']['marker'] + ':' + config['configurable']['langgraph_auth_user']['marker']\n"
        "    builder = StateGraph(State)\n"
        "    builder.add_node('mark', lambda _state: {'marker': marker})\n"
        "    builder.add_edge(START, 'mark')\n"
        "    builder.add_edge('mark', END)\n"
        "    try:\n"
        "        yield builder.compile()\n"
        "    finally:\n"
        "        open(config['metadata']['closed'], 'w').close()\n",
        encoding="utf-8",
    )
    config = tmp_path / "langgraph.json"
    config.write_text(
        json.dumps({"graphs": {"assistant": "graphs.py:get_agent"}}), encoding="utf-8"
    )

    from langgraph_runtime_pg.graph_registry import GraphRegistry

    registry = GraphRegistry.from_path(config)
    closed = tmp_path / "closed"
    from langgraph_runtime_pg.graph_executor import thread_config

    run_config = thread_config(
        "thread-a",
        metadata={"marker": "run-a", "closed": str(closed)},
        runtime_context={
            "user_id": "user-a",
            "tenant_id": "tenant-a",
            "project_id": "project-a",
            "role": "developer",
            "permissions": ["runs:write"],
            "auth_user": {"identity": "user-a", "marker": "signed-user"},
        },
    )
    async with registry.open("assistant", run_config) as graph:
        result = await graph.ainvoke({"marker": "input"})
    assert result == {"marker": "run-a:signed-user"}
    assert closed.is_file()


@pytest.mark.asyncio
async def test_executor_uses_public_v2_invoke() -> None:
    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    result = await invoke_graph(_graph(), {"value": 1}, config=thread_config("thread-1"))
    assert result.value == {"value": 2}
    assert result.interrupts == ()


@pytest.mark.asyncio
async def test_executor_passes_public_durability_to_invoke_and_stream() -> None:
    from langgraph.types import GraphOutput

    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    class RecordingGraph:
        calls: list[tuple[str | None, object, object]] = []

        async def ainvoke(
            self, _input, *, config, durability, interrupt_before, interrupt_after, version
        ):
            del config, version
            self.calls.append((durability, interrupt_before, interrupt_after))
            return GraphOutput(value={"value": 1}, interrupts=())

        async def astream(
            self, _input, *, config, durability, interrupt_before, interrupt_after, **_kwargs
        ):
            del config
            self.calls.append((durability, interrupt_before, interrupt_after))
            yield {"type": "values", "ns": (), "data": {"value": 1}, "interrupts": ()}

    graph = RecordingGraph()
    config = thread_config("durability-thread")

    async def discard(_event: dict) -> None:
        return None

    await invoke_graph(
        graph,
        {},
        config=config,
        durability="sync",
        interrupt_before=("model",),
    )
    await invoke_graph(
        graph,
        {},
        config=config,
        durability="exit",
        interrupt_after="*",
        on_event=discard,
    )
    assert graph.calls == [("sync", ("model",), None), ("exit", None, "*")]


@pytest.mark.asyncio
async def test_executor_uses_public_v3_stream_without_coroutines() -> None:
    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    result = await invoke_graph(
        _graph(),
        {"value": 1},
        config=thread_config("thread-v3"),
        on_event=collect,
    )
    assert result.value == {"value": 2}
    assert result.interrupts == ()
    assert events and all(not hasattr(item.get("data"), "__await__") for item in events)
    assert events[0]["method"] == events[0]["event"] == "values"
    assert events[0]["params"]["namespace"] == []
    assert events[0]["params"]["data"] == {"value": 1}


@pytest.mark.asyncio
async def test_executor_captures_all_v2_stream_parts() -> None:
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.config import get_stream_writer

    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    model = FakeMessagesListChatModel(responses=[AIMessage(content="streamed")])

    def emit(state: _State) -> _State:
        model.invoke([])
        get_stream_writer()({"progress": 1})
        return {"value": state["value"] + 1}

    builder = StateGraph(_State)
    builder.add_node("emit", emit)
    builder.add_edge(START, "emit")
    builder.add_edge("emit", END)
    graph = builder.compile(checkpointer=MemorySaver())
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    result = await invoke_graph(
        graph,
        {"value": 1},
        config=thread_config("all-v2-modes"),
        on_event=collect,
    )

    methods = {event["method"] for event in events}
    assert {"values", "updates", "messages", "custom", "checkpoints", "tasks", "debug"} <= methods
    assert result.value == {"value": 2}
    assert all(event["params"]["namespace"] == [] for event in events)
    custom = next(event for event in events if event["method"] == "custom")
    assert custom["data"] == {"progress": 1}
    message = next(event for event in events if event["method"] == "messages")
    assert message["data"][0]["content"] == "streamed"
    assert message["data"][1]["langgraph_node"] == "emit"
    values = [event for event in events if event["method"] == "values"]
    assert values[-1]["data"] == {"value": 2}


@pytest.mark.asyncio
async def test_executor_preserves_subgraph_namespace_and_interrupts() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt

    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    child_builder = StateGraph(_State)
    child_builder.add_node("child_step", lambda state: {"value": state["value"] + 1})
    child_builder.add_edge(START, "child_step")
    child_builder.add_edge("child_step", END)
    child = child_builder.compile()
    parent_builder = StateGraph(_State)
    parent_builder.add_node("child", child)
    parent_builder.add_edge(START, "child")
    parent_builder.add_edge("child", END)
    events: list[dict] = []

    async def collect(event: dict) -> None:
        events.append(event)

    result = await invoke_graph(
        parent_builder.compile(checkpointer=MemorySaver()),
        {"value": 1},
        config=thread_config("nested-v2"),
        on_event=collect,
    )
    assert result.value == {"value": 2}
    assert any(event["params"]["namespace"] for event in events)

    interrupt_builder = StateGraph(_State)
    interrupt_builder.add_node("approval", lambda _state: {"value": interrupt({"ok": True})})
    interrupt_builder.add_edge(START, "approval")
    interrupt_builder.add_edge("approval", END)
    interrupted = await invoke_graph(
        interrupt_builder.compile(checkpointer=MemorySaver()),
        {"value": 1},
        config=thread_config("interrupt-v2"),
        on_event=collect,
    )
    assert interrupted.interrupts
    assert any(event["params"]["interrupts"] for event in events if event["method"] == "values")


def test_langgraph_v3_inprocess_projections_and_interleave() -> None:
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.config import get_stream_writer
    from langgraph.stream._types import ProtocolEvent, StreamTransformer
    from langgraph.stream.stream_channel import StreamChannel

    class AuditProjection(StreamTransformer):
        required_stream_modes = ("custom", "updates")

        def __init__(self, scope: tuple[str, ...] = ()) -> None:
            super().__init__(scope)
            self.channel: StreamChannel[dict[str, int]] = StreamChannel("audit")

        def init(self) -> dict[str, StreamChannel[dict[str, int]]]:
            return {"audit": self.channel}

        def process(self, event: ProtocolEvent) -> bool:
            if event["method"] == "custom":
                self.channel.push(event["params"]["data"])
            return True

    model = FakeMessagesListChatModel(responses=[AIMessage(content="streamed")] * 4)

    def emit(state: _State) -> _State:
        model.invoke([])
        get_stream_writer()({"progress": state["value"]})
        return {"value": state["value"] + 1}

    builder = StateGraph(_State)
    builder.add_node("emit", emit)
    builder.add_edge(START, "emit")
    builder.add_edge("emit", END)
    graph = builder.compile()

    raw_stream = graph.stream_events({"value": 1}, version="v3", transformers=[AuditProjection])
    raw_events = list(raw_stream)
    assert {event["method"] for event in raw_events} >= {
        "values",
        "updates",
        "messages",
        "custom",
        "custom:audit",
    }
    assert raw_stream.output == {"value": 2}

    interleaved = graph.stream_events({"value": 1}, version="v3", transformers=[AuditProjection])
    assert list(interleaved.interleave("values", "audit")) == [
        ("values", {"value": 1}),
        ("audit", {"progress": 1}),
        ("values", {"value": 2}),
    ]
    assert "audit" in interleaved.extensions

    messages_stream = graph.stream_events({"value": 1}, version="v3")
    messages = list(messages_stream.messages)
    assert len(messages) == 1
    assert messages[0].text.get() == "streamed"

    async def collect(source: AsyncIterable[Any]) -> list[Any]:
        return [item async for item in source]

    async def verify_async_projections() -> None:
        stream = await graph.astream_events(
            {"value": 1}, version="v3", transformers=[AuditProjection]
        )
        events, values = await asyncio.gather(collect(stream), collect(stream.values))
        assert {event["method"] for event in events} >= {"values", "messages", "custom:audit"}
        assert values == [{"value": 1}, {"value": 2}]
        assert await stream.output() == {"value": 2}

    asyncio.run(verify_async_projections())


def test_langgraph_v3_inprocess_subgraphs_and_interrupts() -> None:
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import interrupt

    child_builder = StateGraph(_State)
    child_builder.add_node("child_step", lambda state: {"value": state["value"] + 1})
    child_builder.add_edge(START, "child_step")
    child_builder.add_edge("child_step", END)
    parent_builder = StateGraph(_State)
    parent_builder.add_node("child", child_builder.compile())
    parent_builder.add_edge(START, "child")
    parent_builder.add_edge("child", END)

    parent = parent_builder.compile()
    lifecycle_stream = parent.stream_events({"value": 1}, version="v3")
    lifecycle = [event for event in lifecycle_stream if event["method"] == "lifecycle"]
    assert [event["params"]["data"]["event"] for event in lifecycle] == ["started", "completed"]
    assert lifecycle[0]["params"]["data"]["namespace"]

    subgraph_stream = parent.stream_events({"value": 1}, version="v3")
    subgraphs = list(subgraph_stream.subgraphs)
    assert len(subgraphs) == 1
    assert subgraphs[0].path and subgraphs[0].graph_name == "child"
    assert subgraphs[0].status == "completed"
    assert subgraphs[0].output == {"value": 2}

    interrupt_builder = StateGraph(_State)
    interrupt_builder.add_node("approval", lambda _state: {"value": interrupt({"ok": True})})
    interrupt_builder.add_edge(START, "approval")
    interrupt_builder.add_edge("approval", END)
    interrupted = interrupt_builder.compile(checkpointer=MemorySaver()).stream_events(
        {"value": 1},
        {"configurable": {"thread_id": "inprocess-v3-interrupt"}},
        version="v3",
    )
    assert list(interrupted.values) == [{"value": 1}, {"value": 1}]
    assert interrupted.interrupted
    assert interrupted.interrupts
    assert interrupted.output == {"value": 1}


def test_thread_config_carries_agent_server_runtime_identity() -> None:
    from langgraph_runtime_pg.graph_executor import thread_config

    config = thread_config(
        "thread-runtime",
        assistant_id="assistant-1",
        graph_id="assistant",
        runtime_context={
            "user_id": "user-1",
            "tenant_id": "tenant-1",
            "project_id": "project-1",
            "role": "operator",
            "permissions": ["runs:write"],
        },
    )
    assert config["configurable"]["thread_id"] == "thread-runtime"
    runtime = config["configurable"]["__pregel_runtime"]
    assert runtime.server_info.graph_id == "assistant"
    assert runtime.server_info.user["identity"] == "user-1"
