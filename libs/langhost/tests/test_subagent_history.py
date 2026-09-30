from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Annotated, TypedDict
from uuid import uuid4

import pytest
from langgraph.channels import DeltaChannel
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from starlette.requests import Request

from langhost import core_api


class RootState(TypedDict):
    messages: Annotated[
        list[str],
        DeltaChannel(lambda state, writes: state + [item for batch in writes for item in batch]),
    ]


@pytest.mark.asyncio
async def test_subagent_namespace_state_and_history(monkeypatch):
    tid = uuid4()
    root_config = {"configurable": {"thread_id": str(tid), "checkpoint_ns": ""}}
    subagent_ns = "tools:call_subagent_abc123"

    saver = InMemorySaver()

    # 1. 模拟根图执行，保存在根命名空间
    root_graph = (
        StateGraph(RootState)
        .add_node("agent", lambda state: {"messages": ["call task tool"]})
        .add_edge(START, "agent")
        .add_edge("agent", END)
        .compile(checkpointer=saver)
    )
    await root_graph.ainvoke({"messages": ["user prompt"]}, root_config)

    # 2. 模拟 Subagent 在独立命名空间下的执行历史（写入多个 step checkpoint）
    # Step 1: Subagent 接收指令并发出 read_file 工具调用
    cp_id_1 = "00000000-0000-0000-0000-000000000001"
    config_step1 = {
        "configurable": {
            "thread_id": str(tid),
            "checkpoint_ns": subagent_ns,
            "checkpoint_id": cp_id_1,
        }
    }
    cp1 = empty_checkpoint()
    cp1["id"] = cp_id_1
    cp1["channel_values"] = {
        "messages": [
            {"role": "human", "content": "investigate code"},
            {"role": "ai", "tool_calls": [{"name": "read_file", "args": {"path": "main.py"}}]},
        ]
    }
    cp1["channel_versions"] = {"messages": 1}
    await saver.aput(config_step1, cp1, {"step": 1}, {"messages": 1})

    # Step 2: Subagent 获得 read_file 返回并给出最终结论
    cp_id_2 = "00000000-0000-0000-0000-000000000002"
    config_step2 = {
        "configurable": {
            "thread_id": str(tid),
            "checkpoint_ns": subagent_ns,
            "checkpoint_id": cp_id_2,
        }
    }
    cp2 = empty_checkpoint()
    cp2["id"] = cp_id_2
    cp2["channel_values"] = {
        "messages": [
            {"role": "human", "content": "investigate code"},
            {"role": "ai", "tool_calls": [{"name": "read_file", "args": {"path": "main.py"}}]},
            {"role": "tool", "name": "read_file", "content": "def main(): pass"},
            {"role": "ai", "content": "investigation finished"},
        ]
    }
    cp2["channel_versions"] = {"messages": 2}
    await saver.aput(config_step2, cp2, {"step": 2}, {"messages": 2})

    # 3. 准备 mock 的 graph_registry 与 get_checkpointer
    @asynccontextmanager
    async def opened(graph_id, run_config):
        # 静态注册中只有 root_graph，子图在 registry 中并不存在
        assert graph_id == "root_agent"
        yield root_graph

    async def get_thread(request):
        return (
            SimpleNamespace(
                graph_id="root_agent",
                metadata_={},
                values_={"messages": ["root cached messages"]},
                interrupts={},
            ),
            None,
            tid,
        )

    monkeypatch.setattr(core_api, "_get_thread", get_thread)
    monkeypatch.setattr(core_api, "get_checkpointer", lambda: saver)

    app = SimpleNamespace(
        state=SimpleNamespace(
            graph_registry=SimpleNamespace(open=opened, ids=lambda: ("root_agent",))
        )
    )

    # 4. 场景 1：根图默认查询（回归测试）
    req_root = Request(
        {
            "type": "http",
            "method": "GET",
            "app": app,
            "path_params": {},
            "query_string": b"",
        }
    )
    root_state = json.loads((await core_api.threads_state(req_root)).body)
    assert root_state["checkpoint"]["checkpoint_ns"] == ""
    assert root_state["values"]["messages"] == ["user prompt", "call task tool"]

    # 5. 场景 2：通过 POST /state/checkpoint 指定子图 checkpoint_ns 查询最新状态
    req_sub_post = Request(
        {
            "type": "http",
            "method": "POST",
            "app": app,
            "path_params": {},
            "query_string": b"",
        }
    )
    req_sub_post._json = {"checkpoint": {"checkpoint_ns": subagent_ns}}
    sub_state_post = json.loads((await core_api.threads_state(req_sub_post)).body)
    assert sub_state_post["checkpoint"]["checkpoint_ns"] == subagent_ns
    assert sub_state_post["checkpoint"]["checkpoint_id"] == cp_id_2
    assert len(sub_state_post["values"]["messages"]) == 4
    assert sub_state_post["values"]["messages"][1]["tool_calls"][0]["name"] == "read_file"
    assert sub_state_post["values"]["messages"][3]["content"] == "investigation finished"

    # 6. 场景 3：通过 GET /state?checkpoint_ns=... 查询最新状态
    req_sub_get = Request(
        {
            "type": "http",
            "method": "GET",
            "app": app,
            "path_params": {},
            "query_string": f"checkpoint_ns={subagent_ns}".encode(),
        }
    )
    sub_state_get = json.loads((await core_api.threads_state(req_sub_get)).body)
    assert sub_state_get["checkpoint"]["checkpoint_ns"] == subagent_ns
    assert sub_state_get["values"] == sub_state_post["values"]

    # 7. 场景 4：指定特定 checkpoint_id 获取子图历史某一步状态
    req_sub_step1 = Request(
        {
            "type": "http",
            "method": "POST",
            "app": app,
            "path_params": {},
            "query_string": b"",
        }
    )
    req_sub_step1._json = {
        "checkpoint": {
            "checkpoint_ns": subagent_ns,
            "checkpoint_id": cp_id_1,
        }
    }
    sub_step1_state = json.loads((await core_api.threads_state(req_sub_step1)).body)
    assert sub_step1_state["checkpoint"]["checkpoint_id"] == cp_id_1
    assert len(sub_step1_state["values"]["messages"]) == 2

    # 8. 场景 5：通过 POST /history 指定子图 checkpoint_ns 查询历史列表
    req_hist_post = Request(
        {
            "type": "http",
            "method": "POST",
            "app": app,
            "path_params": {},
            "query_string": b"",
        }
    )
    req_hist_post._json = {"checkpoint": {"checkpoint_ns": subagent_ns}, "limit": 10}
    sub_hist = json.loads((await core_api.threads_history(req_hist_post)).body)
    assert len(sub_hist) == 2
    # 历史列表按倒序排列（最新在前）
    assert sub_hist[0]["checkpoint"]["checkpoint_id"] == cp_id_2
    assert sub_hist[1]["checkpoint"]["checkpoint_id"] == cp_id_1
    for item in sub_hist:
        assert item["checkpoint"]["checkpoint_ns"] == subagent_ns

    # 9. 场景 6：查询不存在的子图命名空间，返回空 values，不被根图数据污染
    req_nonexistent = Request(
        {
            "type": "http",
            "method": "GET",
            "app": app,
            "path_params": {},
            "query_string": b"checkpoint_ns=tools:nonexistent_call",
        }
    )
    nonexistent_state = json.loads((await core_api.threads_state(req_nonexistent)).body)
    assert nonexistent_state["checkpoint"]["checkpoint_ns"] == "tools:nonexistent_call"
    assert nonexistent_state["values"] == {}
    assert nonexistent_state["interrupts"] == []
