from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any, TypedDict

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


@pytest.mark.asyncio
async def test_executor_uses_public_v2_invoke() -> None:
    from langgraph_runtime_pg.graph_executor import invoke_graph, thread_config

    result = await invoke_graph(_graph(), {"value": 1}, config=thread_config("thread-1"))
    assert result.value == {"value": 2}
    assert result.interrupts == ()


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
