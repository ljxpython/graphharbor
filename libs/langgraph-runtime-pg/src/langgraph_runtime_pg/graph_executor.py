"""Public-API LangGraph execution adapter used by production workers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime, ServerInfo
from langgraph.types import Command, GraphOutput

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def thread_config(
    thread_id: str | None,
    *,
    assistant_id: str | None = None,
    graph_id: str | None = None,
    runtime_context: Mapping[str, Any] | None = None,
) -> RunnableConfig:
    config: RunnableConfig = {"configurable": {}}
    if thread_id:
        config["configurable"]["thread_id"] = thread_id
    if runtime_context:
        user = {
            "identity": str(runtime_context.get("user_id") or "").strip(),
            "tenant_id": str(runtime_context.get("tenant_id") or "").strip(),
            "project_id": str(runtime_context.get("project_id") or "").strip(),
            "role": str(runtime_context.get("role") or "").strip(),
            "permissions": [
                str(item).strip()
                for item in (runtime_context.get("permissions") or [])
                if str(item).strip()
            ],
            "is_authenticated": True,
        }
        if all(user[key] for key in ("identity", "tenant_id", "project_id", "role")):
            config["configurable"]["__pregel_runtime"] = Runtime(
                server_info=ServerInfo(
                    assistant_id=str(assistant_id or ""),
                    graph_id=str(graph_id or ""),
                    user=cast(Any, user),
                )
            )
    return config


async def invoke_graph(
    graph: Any,
    input_value: Any,
    *,
    config: RunnableConfig,
    on_event: EventCallback | None = None,
) -> Any:
    """Run with v2 explicitly and optionally project v3 events."""
    if on_event is None:
        # LangGraph v2 returns GraphOutput(value, interrupts); preserve both fields.
        return await graph.ainvoke(input_value, config=config, version="v2")

    stream = graph.astream_events(input_value, config=config, version="v3")
    if inspect.isawaitable(stream):
        stream = await stream
    async for raw_event in stream:
        event = dict(raw_event)
        if event.get("type") == "event":
            params = event.get("params") or {}
            method = str(event.get("method", "custom"))
            projected = {
                # Keep the v2-friendly ``event`` key while exposing the raw v3
                # protocol envelope for clients that need typed projections.
                "event": method,
                "method": method,
                "data": _jsonable(params.get("data")),
                "namespace": _jsonable(params.get("namespace", [])),
                "timestamp": params.get("timestamp"),
                "seq": event.get("seq"),
                "interrupts": _jsonable(params.get("interrupts", ())),
                "params": _jsonable(params),
            }
        else:
            projected = event
        await on_event(projected)
    output = stream.output() if hasattr(stream, "output") else None
    if inspect.isawaitable(output):
        output = await output
    interrupts = stream.interrupts() if hasattr(stream, "interrupts") else ()
    if inspect.isawaitable(interrupts):
        interrupts = await interrupts
    return GraphOutput(value=output, interrupts=tuple(interrupts or ()))


def resume_command(value: Any) -> Command | None:
    """Convert the public run command envelope to LangGraph's resume input."""
    if not isinstance(value, dict):
        return None
    fields = {key: value[key] for key in ("graph", "update", "resume", "goto") if key in value}
    return Command(**fields) if fields else None


__all__ = ["EventCallback", "invoke_graph", "resume_command", "thread_config"]
