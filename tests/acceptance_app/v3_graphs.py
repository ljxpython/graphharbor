"""Deterministic public-protocol fixtures; no product dependencies or model calls."""

import asyncio
import operator
from typing import Annotated, TypedDict

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Send, interrupt


class State(TypedDict, total=False):
    scenario: str
    label: str
    results: Annotated[list[str], operator.add]


def work(state: State) -> State:
    if state.get("scenario") == "failed":
        raise ValueError("v3 fixture failure")
    if state.get("scenario") == "interrupted":
        answer = interrupt({"question": "Continue fixture?", "label": state.get("label", "done")})
        return {"results": [f"{state.get('label', 'done')}:{answer}"]}
    return {"results": [state.get("label", "done")]}


class Results(TypedDict):
    results: Annotated[list[str], operator.add]


child_builder = StateGraph(State, output_schema=Results)
child_builder.add_node("work", work)
child_builder.add_edge(START, "work")
child_builder.add_edge("work", END)
child = child_builder.compile(name="named-child")

root_builder = StateGraph(State)
root_builder.add_node("child", child)
root_builder.add_edge(START, "child")
root_builder.add_edge("child", END)
edge_graph = root_builder.compile(name="named-root")


def fanout(state: State) -> list[Send]:
    return [Send("child", {**state, "label": label}) for label in ("alpha", "beta")]


class FanoutState(TypedDict):
    results: Annotated[list[str], operator.add]


fanout_builder = StateGraph(FanoutState)
fanout_builder.add_node("child", child)
fanout_builder.add_conditional_edges(START, fanout)
fanout_builder.add_edge("child", END)
send_graph = fanout_builder.compile(name="fanout-root")

nested_builder = StateGraph(State)
nested_builder.add_node("outer", edge_graph)
nested_builder.add_edge(START, "outer")
nested_builder.add_edge("outer", END)
nested_graph = nested_builder.compile(name="nested-root")


async def catch_failure(state: State, config: RunnableConfig) -> State:
    try:
        await child.ainvoke({"scenario": "failed"}, config)
    except ValueError:
        return {"results": ["caught"]}
    raise AssertionError("child must fail")


caught_builder = StateGraph(State)
caught_builder.add_node("catch", catch_failure)
caught_builder.add_edge(START, "catch")
caught_builder.add_edge("catch", END)
caught_graph = caught_builder.compile(name="caught-root")

multi_builder = StateGraph(State)
multi_builder.add_node("child", child)
multi_builder.add_conditional_edges(START, fanout)
multi_builder.add_edge("child", END)
multi_graph = multi_builder.compile(name="multi-root")


async def slow(state: State) -> State:
    await asyncio.sleep(float(state.get("label", "5")))
    return {"results": ["slow-done"]}


slow_builder = StateGraph(State)
slow_builder.add_node("slow", slow)
slow_builder.add_edge(START, "slow")
slow_builder.add_edge("slow", END)
slow_graph = slow_builder.compile(name="slow-root")


agent_child = create_agent(
    FakeListChatModel(responses=["child-result"], sleep=0.01),
    name="named-child",
)


@tool
async def delegate(label: str, config: RunnableConfig) -> str:
    """Run one bounded named child graph."""
    result = await agent_child.ainvoke({"messages": [("user", label)]}, config=config)
    return str(result["messages"][-1].content)


def dispatch(state: MessagesState) -> dict:
    del state
    get_stream_writer()({"fixture": "dispatch"})
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delegate", "args": {"label": label}, "id": f"dispatch-{label}"}
                    for label in ("alpha", "beta")
                ],
            )
        ]
    }


tools_builder = StateGraph(MessagesState)
tools_builder.add_node("dispatch", dispatch)
tools_builder.add_node("tools", ToolNode([delegate]))
tools_builder.add_edge(START, "dispatch")
tools_builder.add_conditional_edges(
    "dispatch",
    lambda state: [
        Send("tools", {"__type": "tool_call_with_context", "tool_call": call, "state": state})
        for call in state["messages"][-1].tool_calls
    ],
)
tools_builder.add_edge("tools", END)
tool_graph = tools_builder.compile(name="tools-root")


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self


projection_graph = create_agent(
    ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "delegate", "args": {"label": "alpha"}, "id": "projection-alpha"}
                ],
            ),
            AIMessage(content="projection-complete"),
        ]
    ),
    tools=[delegate],
    name="projection-root",
)
