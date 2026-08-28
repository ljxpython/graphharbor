"""Small, versioned graphs used by the local compatibility acceptance suite."""

from __future__ import annotations

import json
import os

import httpx
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool as langchain_tool
from langchain_openai import ChatOpenAI
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from typing_extensions import TypedDict


class BasicState(TypedDict, total=False):
    value: int
    trace: list[str]


def _basic_node(state: BasicState) -> BasicState:
    return {"value": int(state.get("value", 0)) + 1, "trace": ["basic"]}


_basic_builder = StateGraph(BasicState)
_basic_builder.add_node("increment", _basic_node)
_basic_builder.add_edge(START, "increment")
_basic_builder.add_edge("increment", END)
basic = _basic_builder.compile()


class SubgraphState(TypedDict, total=False):
    steps: list[str]


def _child_node(state: SubgraphState) -> SubgraphState:
    return {"steps": [*(state.get("steps") or []), "child"]}


_child_builder = StateGraph(SubgraphState)
_child_builder.add_node("child_step", _child_node)
_child_builder.add_edge(START, "child_step")
_child_builder.add_edge("child_step", END)
_child = _child_builder.compile()

_subgraph_builder = StateGraph(SubgraphState)
_subgraph_builder.add_node("child", _child)
_subgraph_builder.add_edge(START, "child")
_subgraph_builder.add_edge("child", END)
subgraph = _subgraph_builder.compile()


class HitlState(TypedDict, total=False):
    question: str
    approved: object


def _ask_for_approval(state: HitlState) -> HitlState:
    answer = interrupt({"question": state.get("question", "Approve this run?")})
    return {"approved": answer}


_hitl_builder = StateGraph(HitlState)
_hitl_builder.add_node("approval", _ask_for_approval)
_hitl_builder.add_edge(START, "approval")
_hitl_builder.add_edge("approval", END)
hitl = _hitl_builder.compile()


@langchain_tool("multiply")
def _multiply(a: int, b: int) -> int:
    """Multiply two integers for the deterministic tool fixture."""
    return a * b


class ToolState(TypedDict, total=False):
    a: int
    b: int
    tool_calls: list[dict[str, object]]
    result: int


def _tool_node(state: ToolState) -> ToolState:
    a = int(state.get("a", 3))
    b = int(state.get("b", 4))
    result = int(_multiply.invoke({"a": a, "b": b}))
    return {
        "tool_calls": [{"name": "multiply", "args": {"a": a, "b": b}}],
        "result": result,
    }


_tool_builder = StateGraph(ToolState)
_tool_builder.add_node("tool", _tool_node)
_tool_builder.add_edge(START, "tool")
_tool_builder.add_edge("tool", END)
tool = _tool_builder.compile()


class StreamingState(TypedDict, total=False):
    value: int


_streaming_model = FakeMessagesListChatModel(responses=[AIMessage(content="streamed")])


def _streaming_node(state: StreamingState) -> StreamingState:
    _streaming_model.invoke([])
    get_stream_writer()({"progress": 1})
    return {"value": int(state.get("value", 0)) + 1}


_streaming_builder = StateGraph(StreamingState)
_streaming_builder.add_node("emit", _streaming_node)
_streaming_builder.add_edge(START, "emit")
_streaming_builder.add_edge("emit", END)
streaming_all_modes = _streaming_builder.compile()


class ChatState(TypedDict, total=False):
    messages: list[dict[str, str]]
    response: str
    provider: str
    model: str
    token_count: int
    provider_streamed: bool


def _proxy_url() -> str:
    value = os.environ.get("DEEPSEEK_PROXY_URL", "").strip().rstrip("/")
    if not value:
        raise RuntimeError("DEEPSEEK_PROXY_URL is required for the chat acceptance graph")
    return value if value.endswith("/chat/completions") else f"{value}/chat/completions"


def _langchain_model() -> ChatOpenAI:
    api_key = os.environ.get("DEEPSEEK_PROXY_API_KEY", "acceptance-unconfigured").strip()
    model = os.environ.get("DEEPSEEK_PROXY_DEFAULT_MODEL", "acceptance-unconfigured").strip()
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=os.environ.get("DEEPSEEK_PROXY_URL", "").strip().rstrip("/"),
        temperature=0,
        streaming=True,
        use_responses_api=False,
    )


@langchain_tool
def lookup_fact(topic: str) -> str:
    """Return a fixed project fact so agent tool loops are easy to assert."""
    del topic
    return "GraphHarbor uses LangGraph, PostgreSQL persistence, and Redis transport."


langchain_agent = create_agent(
    model=_langchain_model(),
    tools=[lookup_fact],
    system_prompt="Use lookup_fact once before answering. Keep the final answer concise.",
    name="langchain_acceptance_agent",
)


deep_agent = create_deep_agent(
    model=_langchain_model(),
    tools=[lookup_fact],
    system_prompt="Use lookup_fact once before answering. Do not use filesystem or shell tools.",
    name="deep_agent_acceptance_agent",
)


async def _deepseek_completion(messages: list[dict[str, str]]) -> tuple[str, int]:
    api_key = os.environ.get("DEEPSEEK_PROXY_API_KEY", "").strip()
    model = os.environ.get("DEEPSEEK_PROXY_DEFAULT_MODEL", "").strip()
    if not api_key or not model:
        raise RuntimeError(
            "DEEPSEEK proxy key and model are required for the chat acceptance graph"
        )
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": messages, "stream": True, "temperature": 0}
    text: list[str] = []
    chunks = 0
    async with (
        httpx.AsyncClient(timeout=90) as client,
        client.stream("POST", _proxy_url(), headers=headers, json=payload) as response,
    ):
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            item = json.loads(data)
            choices = item.get("choices") or []
            delta = (choices[0].get("delta") or {}).get("content") if choices else None
            if delta:
                text.append(str(delta))
                chunks += 1
    return "".join(text), chunks


async def _chat_node(state: ChatState) -> ChatState:
    messages = state.get("messages") or [{"role": "user", "content": "Reply with READY."}]
    response, chunks = await _deepseek_completion(messages)
    # The current runtime projects v3 graph events. Keep provider stream facts in
    # state so the acceptance result can distinguish model streaming from SSE.
    return {
        "response": response,
        "provider": "deepseek",
        "model": os.environ.get("DEEPSEEK_PROXY_DEFAULT_MODEL", ""),
        "token_count": chunks,
        "provider_streamed": chunks > 0,
    }


_chat_builder = StateGraph(ChatState)
_chat_builder.add_node("chat", _chat_node)
_chat_builder.add_edge(START, "chat")
_chat_builder.add_edge("chat", END)
chat = _chat_builder.compile()


__all__ = ["basic", "chat", "deep_agent", "hitl", "langchain_agent", "subgraph", "tool"]
