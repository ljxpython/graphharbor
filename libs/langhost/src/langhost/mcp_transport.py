"""GraphHarbor's stateless MCP Streamable HTTP transport."""

from __future__ import annotations

import inspect
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, is_dataclass
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from langgraph_runtime_pg.auth import Principal, principal_from_scope
from langgraph_runtime_pg.graph_executor import thread_config


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
    if is_dataclass(value):
        return _jsonable(asdict(value))  # type: ignore[arg-type]
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _runtime_context(principal: Principal | None) -> dict[str, Any] | None:
    if principal is None:
        return None
    return {
        "user_id": principal.subject,
        "tenant_id": principal.tenant_id,
        "project_id": principal.project_id,
        "role": next(iter(principal.roles), "user"),
        "permissions": sorted(principal.scopes),
    }


def _input_signature(name: str) -> inspect.Signature:
    del name
    parameters = [
        inspect.Parameter(
            "ctx",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=Context,
        )
    ]
    parameters.append(
        inspect.Parameter(
            "input",
            inspect.Parameter.KEYWORD_ONLY,
            annotation=dict[str, Any],
            default=inspect.Parameter.empty,
        )
    )
    return inspect.Signature(parameters)


@asynccontextmanager
async def _open_graph(registry: Any, graph_id: str, config: RunnableConfig):
    opener = getattr(registry, "open", None)
    if callable(opener):
        async with opener(graph_id, config) as graph:
            yield graph
        return
    getter = getattr(registry, "get", None)
    if not callable(getter):
        raise RuntimeError("graph registry must provide open() or get()")
    yield getter(graph_id)


def _tool_for_graph(graph_id: str, registry: Any):
    async def invoke(ctx: Context, **kwargs: Any) -> dict[str, Any]:
        try:
            request_context = ctx.request_context
        except (LookupError, ValueError):
            request_context = None
        request = getattr(request_context, "request", None)
        principal = principal_from_scope(request.scope) if request is not None else None
        payload = kwargs["input"]
        config = thread_config(
            f"mcp-{uuid4()}",
            graph_id=graph_id,
            runtime_context=_runtime_context(principal),
        )
        async with _open_graph(registry, graph_id, config) as graph:
            result = await graph.ainvoke(payload, config=config, version="v2")
        return _jsonable(result)

    invoke.__name__ = f"graph_{graph_id.replace('-', '_')}"
    invoke.__doc__ = f"Invoke the {graph_id} LangGraph agent."
    invoke.__signature__ = _input_signature(graph_id)  # type: ignore[attr-defined]
    return invoke


def create_mcp_transport(registry: Any) -> tuple[FastMCP, Any]:
    """Build the MCP server and its mounted Starlette application."""
    server = FastMCP(
        "graphharbor",
        instructions="GraphHarbor LangGraph agents exposed through MCP.",
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                item.strip()
                for item in os.environ.get(
                    "GRAPHHARBOR_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*"
                ).split(",")
                if item.strip()
            ],
        ),
    )
    for graph_id in registry.ids():
        server.add_tool(_tool_for_graph(graph_id, registry), name=graph_id)
    return server, server.streamable_http_app()


__all__ = ["create_mcp_transport"]
