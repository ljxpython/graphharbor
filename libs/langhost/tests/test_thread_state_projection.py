from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated, TypedDict
from uuid import uuid4

import pytest
from langgraph.channels import DeltaChannel
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from starlette.requests import Request

from langhost import core_api


class State(TypedDict):
    messages: Annotated[
        list[str],
        DeltaChannel(lambda state, writes: state + [item for batch in writes for item in batch]),
    ]


@pytest.mark.asyncio
async def test_state_and_history_reconstruct_delta_messages_and_interrupts(monkeypatch):
    tid = uuid4()
    config = {"configurable": {"thread_id": str(tid)}}
    saver = InMemorySaver()
    graph = (
        StateGraph(State)
        .add_node("read", lambda state: {"messages": ["read files"]})
        .add_node("review", lambda state: {"messages": [interrupt("approve write")]})
        .add_edge(START, "read")
        .add_edge("read", "review")
        .add_edge("review", END)
        .compile(checkpointer=saver)
    )
    await graph.ainvoke({"messages": ["user request"]}, config)
    raw = await saver.aget_tuple(config)
    assert "messages" not in raw.checkpoint["channel_values"]

    @asynccontextmanager
    async def opened(graph_id, run_config):
        assert graph_id == "agent"
        yield graph

    async def get_thread(request):
        return SimpleNamespace(graph_id="agent", metadata_={}), None, tid

    monkeypatch.setattr(core_api, "_get_thread", get_thread)
    app = SimpleNamespace(
        state=SimpleNamespace(graph_registry=SimpleNamespace(open=opened, ids=lambda: ("agent",)))
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "app": app,
            "path_params": {},
            "query_string": b"limit=10",
        }
    )
    state = json.loads((await core_api.threads_state(request)).body)
    assert state["values"] == {"messages": ["user request", "read files"]}
    assert state["next"] == ["review"]
    assert state["tasks"][0]["name"] == "review"
    assert state["interrupts"][0]["value"] == "approve write"
    history = json.loads((await core_api.threads_history(request)).body)
    assert history[0]["values"] == state["values"]
    assert history[0]["interrupts"] == state["interrupts"]

    checkpoint_id = state["checkpoint"]["checkpoint_id"]
    await graph.ainvoke(Command(resume="approved"), config)
    current = json.loads((await core_api.threads_state(request)).body)
    assert current["values"]["messages"][-1] == "approved"
    assert not current["next"] and not current["interrupts"]
    request.scope["path_params"] = {"checkpoint_id": checkpoint_id}
    historical = json.loads((await core_api.threads_state(request)).body)
    assert historical["values"] == state["values"]
