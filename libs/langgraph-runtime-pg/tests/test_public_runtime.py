from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

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
