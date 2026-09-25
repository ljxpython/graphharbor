"""Public-API LangGraph execution adapter used by production workers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime, ServerInfo
from langgraph.stream import (
    CheckpointsTransformer,
    CustomTransformer,
    DebugTransformer,
    TasksTransformer,
    UpdatesTransformer,
)
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
        raw_auth_user = runtime_context.get("auth_user")
        user = dict(raw_auth_user) if isinstance(raw_auth_user, Mapping) else {}
        if user.get("identity"):
            config["configurable"]["langgraph_auth_user"] = user
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
    durability: Durability | None = None,
    interrupt_before: str | tuple[str, ...] | None = None,
    interrupt_after: str | tuple[str, ...] | None = None,
) -> Any:
    """Run once using LangGraph's native typed event stream and durable output."""
    if on_event is None:
        # LangGraph v2 returns GraphOutput(value, interrupts); preserve both fields.
        return await graph.ainvoke(
            input_value,
            config=config,
            context=config.get("context"),
            durability=durability,
            interrupt_before=interrupt_before,
            interrupt_after=interrupt_after,
            version="v2",
        )

    stream = await graph.astream_events(
        input_value,
        config=config,
        context=config.get("context"),
        durability=durability,
        interrupt_before=interrupt_before,
        interrupt_after=interrupt_after,
        version="v3",
        transformers=[
            UpdatesTransformer,
            CustomTransformer,
            CheckpointsTransformer,
            TasksTransformer,
            DebugTransformer,
        ],
    )
    scope_names: dict[tuple[str, ...], str] = {}
    async with stream:
        async for part in stream:
            event = _jsonable(part)
            params = event["params"]
            method = event["method"]
            if method == "lifecycle":
                data = params["data"]
                scope = tuple(data.get("namespace") or params["namespace"])
                if isinstance(data.get("graph_name"), str):
                    scope_names[scope] = data["graph_name"]
                elif scope in scope_names:
                    params["data"] = {**data, "graph_name": scope_names[scope]}
            # Native v3 projects child lifecycle at the root envelope; the
            # affected scope lives in data.namespace. Only suppress actual root
            # lifecycle, which the worker emits after committing Run state.
            if (
                method == "lifecycle"
                and not params["namespace"]
                and not params["data"].get("namespace")
            ):
                continue
            await on_event(
                {
                    **event,
                    "event": method,
                    "data": params["data"],
                    "namespace": params["namespace"],
                    "interrupts": params.get("interrupts", []),
                }
            )
        return GraphOutput(value=await stream.output(), interrupts=tuple(await stream.interrupts()))


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
