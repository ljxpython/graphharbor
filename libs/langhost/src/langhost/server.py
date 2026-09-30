"""GraphHarbor-owned ASGI boundary for the production profile."""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

import uvicorn
from langgraph_cli.config import validate_config_file
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route

from langgraph_runtime_pg.auth import (
    PrincipalMiddleware,
    principal_from_scope,
)
from langgraph_runtime_pg.checkpoint import get_checkpointer
from langgraph_runtime_pg.database import pool_stats
from langgraph_runtime_pg.graph_registry import GraphRegistry, resolve_within_base_dir
from langgraph_runtime_pg.metrics import prometheus_text, set_gauge
from langgraph_runtime_pg.production import RuntimeReadiness, lifespan as runtime_lifespan
from langgraph_runtime_pg.protocol import official_info_document
from langhost.core_api import (
    assistants_count,
    assistants_create,
    assistants_delete,
    assistants_get,
    assistants_graph,
    assistants_latest,
    assistants_schemas,
    assistants_search,
    assistants_subgraphs,
    assistants_update,
    assistants_versions,
    cron_create_root,
    cron_create_thread,
    cron_delete,
    cron_get,
    cron_update,
    crons_count,
    crons_search,
    register_default_assistants,
    runs_batch,
    runs_cancel,
    runs_cancel_many,
    runs_create_root,
    runs_create_thread,
    runs_delete,
    runs_get,
    runs_join,
    runs_list,
    runs_wait,
    runs_wait_root,
    threads_copy,
    threads_count,
    threads_create,
    threads_delete,
    threads_get,
    threads_history,
    threads_prune,
    threads_search,
    threads_state,
    threads_update,
    threads_update_state,
)
from langhost.mcp_transport import create_mcp_transport
from langhost.protocol_api import protocol_commands, protocol_event_stream
from langhost.store_api import (
    store_delete,
    store_get,
    store_list_namespaces,
    store_put,
    store_search,
)
from langhost.streaming import runs_stream, runs_stream_existing, thread_stream


@asynccontextmanager
async def _empty_context():
    yield


def _plain(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _config_value(config: Any, key: str, default: Any = None) -> Any:
    if isinstance(config, dict):
        return config.get(key, default)
    return getattr(config, key, default)


def _load_symbol(spec: str, base_dir: pathlib.Path) -> Any:
    path_text, separator, symbol = spec.partition(":")
    if not separator or not symbol:
        raise ValueError(f"invalid application path {spec!r}; expected path.py:symbol")
    path = resolve_within_base_dir(base_dir, path_text)
    module_name = f"graphharbor_custom_{path.stem}_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load custom app {path}")
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return getattr(module, symbol)


def _validate_graph_specs(config: Any, base_dir: pathlib.Path) -> None:
    graphs = _config_value(config, "graphs", {}) or {}
    for graph_id, spec in graphs.items():
        graph_path = spec if isinstance(spec, str) else _config_value(spec, "path")
        if not graph_path:
            raise ValueError(f"graph {graph_id!r} has no path")
        path_text = str(graph_path).partition(":")[0]
        path = (base_dir / path_text).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"graph {graph_id!r} does not exist: {path}")


async def _ok(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _live(_: Request) -> JSONResponse:
    return JSONResponse({"live": True})


async def _ready(request: Request) -> JSONResponse:
    readiness = getattr(request.app.state, "readiness", None)
    if readiness is None or not readiness.ready:
        return JSONResponse(
            {
                "ready": False,
                "reason": getattr(readiness, "reason", "not started"),
                "checks": getattr(readiness, "checks", None),
            },
            status_code=503,
        )
    return JSONResponse({"ready": True, "checks": getattr(readiness, "checks", None)})


def _principal(request: Request):
    return principal_from_scope(request.scope)


async def _info(request: Request) -> JSONResponse:
    del request
    return JSONResponse(official_info_document())


def _openapi_document() -> dict[str, Any]:
    return {
        "openapi": "3.1.0",
        "info": {"title": "GraphHarbor Agent Server", "version": "1"},
        "components": {
            "schemas": {
                "ThreadCreate": {
                    "type": "object",
                    "properties": {
                        "thread_id": {"type": "string", "format": "uuid"},
                        "metadata": {"type": "object"},
                        "if_exists": {
                            "type": "string",
                            "enum": ["raise", "do_nothing"],
                            "default": "raise",
                        },
                        "ttl": {
                            "type": "object",
                            "properties": {
                                "strategy": {"type": "string", "enum": ["delete", "keep_latest"]},
                                "ttl": {"type": "number"},
                            },
                        },
                        "supersteps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["updates"],
                                "properties": {"updates": {"type": "array"}},
                            },
                        },
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "required": ["detail"],
                    "properties": {"detail": {"type": "string"}},
                },
                "Thread": {
                    "type": "object",
                    "required": ["thread_id", "created_at", "updated_at", "metadata", "status"],
                    "properties": {
                        "thread_id": {"type": "string", "format": "uuid"},
                        "created_at": {"type": "string", "format": "date-time"},
                        "updated_at": {"type": "string", "format": "date-time"},
                        "metadata": {"type": "object"},
                        "status": {"type": "string"},
                    },
                },
            }
        },
        "paths": {
            "/ok": {"get": {"responses": {"200": {"description": "ready"}}}},
            "/live": {"get": {"responses": {"200": {"description": "alive"}}}},
            "/ready": {"get": {"responses": {"200": {"description": "ready"}}}},
            "/info": {"get": {"responses": {"200": {"description": "capabilities"}}}},
            "/docs": {"get": {"responses": {"200": {"description": "API documentation"}}}},
            "/metrics": {"get": {"responses": {"200": {"description": "Prometheus metrics"}}}},
            "/mcp/": {"delete": {}, "get": {}, "post": {}},
            "/assistants": {"get": {}, "post": {}},
            "/assistants/search": {"post": {}},
            "/assistants/count": {"post": {}},
            "/assistants/{assistant_id}": {"get": {}, "patch": {}, "delete": {}},
            "/assistants/{assistant_id}/versions": {"post": {}},
            "/assistants/{assistant_id}/latest": {"post": {}},
            "/assistants/{assistant_id}/graph": {"get": {}},
            "/assistants/{assistant_id}/schemas": {"get": {}},
            "/assistants/{assistant_id}/subgraphs": {"get": {}},
            "/assistants/{assistant_id}/subgraphs/{namespace}": {"get": {}},
            "/threads": {
                "get": {},
                "post": {
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/ThreadCreate"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Thread created",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Thread"}
                                }
                            },
                        },
                        "409": {
                            "description": "Conflict",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                        "422": {
                            "description": "Validation Error",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                },
            },
            "/threads/search": {"post": {}},
            "/threads/count": {"post": {}},
            "/threads/prune": {"post": {}},
            "/threads/{thread_id}": {"get": {}, "patch": {}, "delete": {}},
            "/threads/{thread_id}/copy": {"post": {}},
            "/threads/{thread_id}/state": {"get": {}, "post": {}, "patch": {}},
            "/threads/{thread_id}/state/checkpoint": {"post": {}},
            "/threads/{thread_id}/state/{checkpoint_id}": {"get": {}},
            "/threads/{thread_id}/history": {"get": {}, "post": {}},
            "/threads/{thread_id}/stream": {"get": {}},
            "/runs": {"post": {}},
            "/runs/wait": {"post": {}},
            "/runs/batch": {"post": {}},
            "/runs/cancel": {"post": {}},
            "/runs/stream": {"post": {}},
            "/runs/{run_id}/stream": {"get": {}},
            "/threads/{thread_id}/runs": {"get": {}, "post": {}},
            "/threads/{thread_id}/runs/wait": {"post": {}},
            "/threads/{thread_id}/runs/stream": {"post": {}},
            "/threads/{thread_id}/runs/{run_id}/stream": {"get": {}},
            "/threads/{thread_id}/runs/{run_id}": {"get": {}, "delete": {}},
            "/threads/{thread_id}/runs/{run_id}/cancel": {"post": {}},
            "/threads/{thread_id}/runs/{run_id}/join": {"get": {}},
            "/threads/{thread_id}/commands": {"post": {}},
            "/threads/{thread_id}/stream/events": {"post": {}},
            "/store/items": {"get": {}, "put": {}, "delete": {}},
            "/store/items/search": {"post": {}},
            "/store/namespaces": {"post": {}},
            "/runs/crons": {"post": {}},
            "/runs/crons/search": {"post": {}},
            "/runs/crons/count": {"post": {}},
            "/runs/crons/{cron_id}": {"get": {}, "patch": {}, "delete": {}},
            "/threads/{thread_id}/runs/crons": {"post": {}},
        },
    }


async def _openapi(request: Request) -> JSONResponse:
    del request
    return JSONResponse(_openapi_document())


async def _docs(request: Request) -> HTMLResponse:
    del request
    configuration = json.dumps(
        {"content": json.dumps(_openapi_document(), separators=(",", ":"))},
        separators=(",", ":"),
    )
    return HTMLResponse(
        "<!doctype html><html><head><title>GraphHarbor API Reference</title></head>"
        f'<body><script id="api-reference"></script><script>var configuration = {configuration};'
        "document.getElementById('api-reference').dataset.configuration = "
        "JSON.stringify(configuration)</script>"
        '<script src="https://cdn.jsdelivr.net/npm/@scalar/api-reference"></script>'
        "</body></html>"
    )


async def _metrics(_: Request):
    from starlette.responses import PlainTextResponse

    for name, value in pool_stats().items():
        set_gauge(f"graphharbor_postgres_pool_{name}", value)
    try:
        from langgraph_runtime_pg.redis_stream import transport_stats

        for name, value in (await transport_stats()).items():
            set_gauge(f"graphharbor_redis_{name}", value)
    except Exception:
        set_gauge("graphharbor_redis_connected", 0)
    return PlainTextResponse(prometheus_text(), media_type="text/plain; version=0.0.4")


async def _assistants(request: Request) -> JSONResponse:
    request._json = dict(request.query_params)
    return await assistants_search(request)


async def _threads(request: Request) -> JSONResponse:
    request._json = dict(request.query_params)
    return await threads_search(request)


def create_app(
    config: dict[str, Any] | Any,
    *,
    base_dir: pathlib.Path | None = None,
    custom_app: Any | None = None,
) -> Starlette:
    base_dir = base_dir or pathlib.Path.cwd()
    readiness = RuntimeReadiness()
    http_config = _config_value(config, "http", {}) or {}
    app_spec = _config_value(http_config, "app") if isinstance(http_config, dict) else None
    custom_app = custom_app or (_load_symbol(app_spec, base_dir) if app_spec else None)
    auth_config = _config_value(config, "auth", {}) or {}
    auth_spec = auth_config if isinstance(auth_config, str) else _config_value(auth_config, "path")
    auth_handler = _load_symbol(auth_spec, base_dir) if auth_spec else None
    mcp_enabled = not bool(_config_value(http_config, "disable_mcp", False))
    mcp_holder: dict[str, Any] = {}

    async def _mcp_dispatch(scope: Any, receive: Any, send: Any) -> None:
        endpoint = mcp_holder.get("app")
        if endpoint is None:
            response = JSONResponse({"detail": "MCP transport is not ready"}, status_code=503)
            await response(scope, receive, send)
            return
        await endpoint(scope, receive, send)

    @asynccontextmanager
    async def lifespan(app: Starlette):
        _validate_graph_specs(config, base_dir)
        app.state.graph_registry = GraphRegistry.from_config(config, base_dir=base_dir)
        readiness.checks = {"graphs": len(app.state.graph_registry) > 0}
        async with runtime_lifespan(app, readiness=readiness):
            await register_default_assistants(app.state.graph_registry)
            if len(app.state.graph_registry) > 0:
                app.state.graph_registry.attach_checkpointer(get_checkpointer())
            mcp_server = None
            if mcp_enabled:
                mcp_server, mcp_holder["app"] = create_mcp_transport(
                    app.state.graph_registry, auth_handler
                )
                mcp_holder["server"] = mcp_server
            async with (
                mcp_server.session_manager.run() if mcp_server is not None else _empty_context()
            ):
                if custom_app is not None and hasattr(
                    getattr(custom_app, "router", None), "lifespan_context"
                ):
                    async with custom_app.router.lifespan_context(custom_app):
                        yield
                else:
                    yield

    routes: list[Any] = [
        Route("/ok", _ok, methods=["GET"]),
        Route("/live", _live, methods=["GET"]),
        Route("/ready", _ready, methods=["GET"]),
        Route("/info", _info, methods=["GET"]),
        Route("/openapi.json", _openapi, methods=["GET"]),
        Route("/docs", _docs, methods=["GET"]),
        Route("/metrics", _metrics, methods=["GET"]),
        Route("/assistants/search", assistants_search, methods=["POST"]),
        Route("/assistants/count", assistants_count, methods=["POST"]),
        Route("/assistants", _assistants, methods=["GET"]),
        Route("/assistants", assistants_create, methods=["POST"]),
        Route("/assistants/{assistant_id}/versions", assistants_versions, methods=["POST"]),
        Route("/assistants/{assistant_id}/latest", assistants_latest, methods=["POST"]),
        Route("/assistants/{assistant_id}/graph", assistants_graph, methods=["GET"]),
        Route("/assistants/{assistant_id}/schemas", assistants_schemas, methods=["GET"]),
        Route(
            "/assistants/{assistant_id}/subgraphs/{namespace}",
            assistants_subgraphs,
            methods=["GET"],
        ),
        Route("/assistants/{assistant_id}/subgraphs", assistants_subgraphs, methods=["GET"]),
        Route("/assistants/{assistant_id}", assistants_get, methods=["GET"]),
        Route("/assistants/{assistant_id}", assistants_update, methods=["PATCH"]),
        Route("/assistants/{assistant_id}", assistants_delete, methods=["DELETE"]),
        Route("/threads/search", threads_search, methods=["POST"]),
        Route("/threads/count", threads_count, methods=["POST"]),
        Route("/threads/prune", threads_prune, methods=["POST"]),
        Route("/threads", _threads, methods=["GET"]),
        Route("/threads", threads_create, methods=["POST"]),
        Route("/threads/{thread_id}/copy", threads_copy, methods=["POST"]),
        Route("/threads/{thread_id}/state/checkpoint", threads_state, methods=["POST"]),
        Route("/threads/{thread_id}/state/{checkpoint_id}", threads_state, methods=["GET"]),
        Route("/threads/{thread_id}/state", threads_state, methods=["GET"]),
        Route("/threads/{thread_id}/state", threads_update_state, methods=["POST", "PATCH"]),
        Route("/threads/{thread_id}/history", threads_history, methods=["GET", "POST"]),
        Route("/threads/{thread_id}/stream", thread_stream, methods=["GET"]),
        Route("/threads/{thread_id}", threads_get, methods=["GET"]),
        Route("/threads/{thread_id}", threads_update, methods=["PATCH"]),
        Route("/threads/{thread_id}", threads_delete, methods=["DELETE"]),
        Route("/runs/{run_id}/stream", runs_stream_existing, methods=["GET"]),
        Route("/runs/stream", runs_stream, methods=["POST"]),
        Route("/runs/batch", runs_batch, methods=["POST"]),
        Route("/runs/cancel", runs_cancel_many, methods=["POST"]),
        Route("/runs/wait", runs_wait_root, methods=["POST"]),
        Route("/runs", runs_create_root, methods=["POST"]),
        Route("/threads/{thread_id}/runs/crons", cron_create_thread, methods=["POST"]),
        Route("/threads/{thread_id}/runs/wait", runs_wait, methods=["POST"]),
        Route("/threads/{thread_id}/runs", runs_list, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}/stream", runs_stream_existing, methods=["GET"]),
        Route("/threads/{thread_id}/runs/stream", runs_stream, methods=["POST"]),
        Route("/threads/{thread_id}/commands", protocol_commands, methods=["POST"]),
        Route("/threads/{thread_id}/stream/events", protocol_event_stream, methods=["POST"]),
        Route("/store/items", store_put, methods=["PUT"]),
        Route("/store/items", store_get, methods=["GET"]),
        Route("/store/items", store_delete, methods=["DELETE"]),
        Route("/store/items/search", store_search, methods=["POST"]),
        Route("/store/namespaces", store_list_namespaces, methods=["POST"]),
        Route("/threads/{thread_id}/runs", runs_create_thread, methods=["POST"]),
        Route("/threads/{thread_id}/runs/{run_id}/cancel", runs_cancel, methods=["POST"]),
        Route("/threads/{thread_id}/runs/{run_id}/join", runs_join, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}", runs_get, methods=["GET"]),
        Route("/threads/{thread_id}/runs/{run_id}", runs_delete, methods=["DELETE"]),
        Route("/runs/crons/search", crons_search, methods=["POST"]),
        Route("/runs/crons/count", crons_count, methods=["POST"]),
        Route("/runs/crons", cron_create_root, methods=["POST"]),
        Route("/runs/crons/{cron_id}", cron_update, methods=["PATCH"]),
        Route("/runs/crons/{cron_id}", cron_get, methods=["GET"]),
        Route("/runs/crons/{cron_id}", cron_delete, methods=["DELETE"]),
    ]
    if mcp_enabled:
        routes.append(Mount("/mcp", app=_mcp_dispatch))
    if custom_app is not None:
        routes.append(Mount("/", app=custom_app))
    cors_config = http_config.get("cors", {}) if isinstance(http_config, dict) else {}
    if not isinstance(cors_config, dict):
        cors_config = {}
    cors_origins = cors_config.get("allow_origins", [])
    cors_methods = cors_config.get("allow_methods", ["GET", "POST", "PATCH", "DELETE", "OPTIONS"])
    cors_headers = cors_config.get(
        "allow_headers", ["Authorization", "Content-Type", "Last-Event-ID"]
    )
    cors_credentials = bool(cors_config.get("allow_credentials", False))
    if isinstance(cors_origins, str):
        cors_origins = [cors_origins]
    if isinstance(cors_methods, str):
        cors_methods = [cors_methods]
    if isinstance(cors_headers, str):
        cors_headers = [cors_headers]
    middleware = [
        Middleware(
            PrincipalMiddleware,
            auth_handler=auth_handler,
            allow_anonymous=os.environ.get("GRAPHHARBOR_ENV", "development") != "production",
        )
    ]
    if cors_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=[str(item) for item in cors_origins],
                allow_methods=[str(item) for item in cors_methods],
                allow_headers=[str(item) for item in cors_headers],
                allow_credentials=cors_credentials,
            )
        )
    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )
    app.state.readiness = readiness
    # Keep the standard langgraph.json extension points observable to custom
    # routes and integration tests while GraphHarbor owns request authentication.
    app.state.auth_handler = auth_handler
    app.state.custom_app = custom_app
    return app


def run_server(host: str, port: int, reload: bool, graphs: dict[str, Any], **kwargs: Any) -> None:
    """Compatibility-shaped entry point owned by GraphHarbor, not langgraph-api."""
    if kwargs.get("__database_uri__"):
        os.environ["DATABASE_URI"] = str(kwargs["__database_uri__"])
    if kwargs.get("__redis_uri__"):
        os.environ["REDIS_URI"] = str(kwargs["__redis_uri__"])
    config = kwargs.pop("config", None) or {"graphs": graphs}
    app = create_app(config, base_dir=kwargs.pop("base_dir", None) or pathlib.Path.cwd())
    uvicorn_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "log_level": str(kwargs.get("server_level", "info")).lower(),
        "reload": reload,
    }
    if not reload and kwargs.get("workers", 1) > 1:
        uvicorn_kwargs["workers"] = kwargs["workers"]
    if kwargs.get("ssl_certfile"):
        uvicorn_kwargs["ssl_certfile"] = kwargs["ssl_certfile"]
        uvicorn_kwargs["ssl_keyfile"] = kwargs["ssl_keyfile"]
    uvicorn.run(app, **uvicorn_kwargs)


def load_config(path: pathlib.Path) -> Any:
    return validate_config_file(path)
