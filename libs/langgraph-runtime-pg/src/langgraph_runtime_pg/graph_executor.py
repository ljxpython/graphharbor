"""Public-API LangGraph execution adapter used by production workers."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from time import time
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime, ServerInfo
from langgraph.types import Command, Durability, GraphOutput

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
_DURABILITY_MODES = frozenset({"sync", "async", "exit"})


def normalize_durability(value: object) -> Durability | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _DURABILITY_MODES:
        raise ValueError("durability must be one of: sync, async, exit")
    return cast(Durability, value)


def normalize_interrupt_nodes(value: object, field: str) -> str | tuple[str, ...] | None:
    if value is None or value == "*":
        return value
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{field} must be '*' or a list of node names")
    return tuple(value)


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
    configurable: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    tags: list[str] | tuple[str, ...] | None = None,
    context: Any = None,
    runtime_context: Mapping[str, Any] | None = None,
    runtime_policy: Mapping[str, Any] | None = None,
) -> RunnableConfig:
    config: RunnableConfig = {"configurable": dict(configurable or {})}
    if thread_id:
        config["configurable"]["thread_id"] = thread_id
    if metadata is not None:
        config["metadata"] = dict(metadata)
    if tags is not None:
        config["tags"] = list(tags)
    if context is not None:
        cast(dict[str, Any], config)["context"] = context
    if runtime_context:
        config["configurable"]["__graphharbor_runtime_context"] = {
            key: value for key, value in runtime_context.items() if key != "auth_user"
        }
        raw_auth_user = runtime_context.get("auth_user")
        user = (
            dict(raw_auth_user)
            if isinstance(raw_auth_user, Mapping)
            else {
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
        )
        if user.get("identity"):
            config["configurable"]["langgraph_auth_user"] = user
            config["configurable"]["__pregel_runtime"] = Runtime(
                server_info=ServerInfo(
                    assistant_id=str(assistant_id or ""),
                    graph_id=str(graph_id or ""),
                    user=cast(Any, user),
                )
            )
    if runtime_policy:
        config["configurable"]["__graphharbor_runtime_policy"] = dict(runtime_policy)
    return config


async def invoke_graph(
    graph: Any,
    input_value: Any,
    *,
    config: RunnableConfig,
    on_event: EventCallback | None = None,
    durability: Durability | None = None,
    interrupt_before: str | tuple[str, ...] | None = None,
    interrupt_after: str | tuple[str, ...] | None = None,
) -> Any:
    """Run once while retaining every documented v2 stream part."""
    if on_event is None:
        # LangGraph v2 returns GraphOutput(value, interrupts); preserve both fields.
        return await graph.ainvoke(
            input_value,
            config=config,
            durability=durability,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
            version="v2",
        )

    stream = graph.astream(
        input_value,
        config=config,
        stream_mode=("values", "updates", "messages", "custom", "checkpoints", "tasks", "debug"),
        subgraphs=True,
        durability=durability,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        version="v2",
    )
    if inspect.isawaitable(stream):
        stream = await stream
    output: Any = None
    interrupts: tuple[Any, ...] = ()
    async for part in stream:
        event = dict(part)
        method = str(event.get("type", "custom"))
        namespace = _jsonable(event.get("ns", ()))
        data = _jsonable(event.get("data"))
        raw_interrupts = event.get("interrupts") or ()
        if method == "values":
            output = data
            interrupts = tuple(raw_interrupts)
        projected = {
            "event": method,
            "method": method,
            "data": data,
            "namespace": namespace,
            "timestamp": int(time() * 1000),
            "interrupts": _jsonable(raw_interrupts),
            "params": {
                "namespace": namespace,
                "timestamp": int(time() * 1000),
                "data": data,
                "interrupts": _jsonable(raw_interrupts),
            },
        }
        await on_event(projected)
    return GraphOutput(value=output, interrupts=tuple(interrupts or ()))


def resume_command(value: Any) -> Command | None:
    """Convert the public run command envelope to LangGraph's resume input."""
    if not isinstance(value, dict):
        return None
    fields = {key: value[key] for key in ("graph", "update", "resume", "goto") if key in value}
    return Command(**fields) if fields else None


__all__ = [
    "EventCallback",
    "invoke_graph",
    "normalize_durability",
    "normalize_interrupt_nodes",
    "resume_command",
    "thread_config",
]
