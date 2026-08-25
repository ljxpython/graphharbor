"""Official Store HTTP handlers backed by GraphHarbor's PostgreSQL Store."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from langgraph_runtime_pg.auth import principal_from_scope
from langgraph_runtime_pg.store import Store


def _namespace_error(namespace: Sequence[str]) -> Response | None:
    if any(not label or "." in label for label in namespace):
        return Response(
            status_code=422,
            content=(
                f"Namespace labels cannot be empty or contain periods. Received: {tuple(namespace)}"
            ),
        )
    return None


def _namespace(request: Request, value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, list) or not all(isinstance(label, str) for label in value):
        return None
    namespace = tuple(value)
    principal = principal_from_scope(request.scope)
    if principal is None:
        return namespace
    return ("__graphharbor__", principal.tenant_id, principal.project_id, *namespace)


def _public_namespace(request: Request, value: Sequence[str]) -> list[str]:
    principal = principal_from_scope(request.scope)
    hidden = 3 if principal is not None else 0
    return list(value[hidden:])


def _item(request: Request, value: Any) -> dict[str, Any]:
    data = value.dict()
    data["namespace"] = _public_namespace(request, data["namespace"])
    return data


async def _body(request: Request) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


async def store_put(request: Request) -> Response:
    payload = await _body(request)
    if payload is None or "key" not in payload or "value" not in payload:
        return JSONResponse({"detail": "namespace, key and value are required"}, status_code=422)
    namespace = _namespace(request, payload.get("namespace"))
    if namespace is None:
        return JSONResponse({"detail": "namespace must be an array of strings"}, status_code=422)
    if error := _namespace_error(namespace[-len(payload["namespace"]) :]):
        return error
    if not isinstance(payload["key"], str) or not isinstance(payload["value"], dict):
        return JSONResponse(
            {"detail": "key must be a string and value must be an object"}, status_code=422
        )
    index = payload.get("index")
    if (
        index is not None
        and index is not False
        and not (isinstance(index, list) and all(isinstance(item, str) for item in index))
    ):
        return JSONResponse(
            {"detail": "index must be null, false, or an array of strings"}, status_code=422
        )
    ttl = payload.get("ttl")
    if ttl is not None and (isinstance(ttl, bool) or not isinstance(ttl, (int, float))):
        return JSONResponse({"detail": "ttl must be a number or null"}, status_code=422)
    await Store().aput(namespace, payload["key"], payload["value"], index=index, ttl=ttl)
    return Response(status_code=204)


async def store_get(request: Request) -> JSONResponse | Response:
    labels = request.query_params.get("namespace", "").split(".")
    namespace = _namespace(request, labels)
    if namespace is None:
        return JSONResponse({"detail": "namespace must be an array of strings"}, status_code=422)
    if error := _namespace_error(labels):
        return error
    key = request.query_params.get("key")
    if not key:
        return JSONResponse({"error": "Key is required"}, status_code=400)
    refresh = request.query_params.get("refresh_ttl")
    item = await Store().aget(
        namespace, key, refresh_ttl=refresh.lower() == "true" if refresh else None
    )
    return JSONResponse(None if item is None else _item(request, item))


async def store_delete(request: Request) -> JSONResponse | Response:
    payload = await _body(request)
    if payload is None or "key" not in payload:
        return JSONResponse({"detail": "namespace and key are required"}, status_code=422)
    namespace = _namespace(request, payload.get("namespace"))
    if namespace is None:
        return JSONResponse({"detail": "namespace must be an array of strings"}, status_code=422)
    if error := _namespace_error(namespace[-len(payload["namespace"]) :]):
        return error
    if not isinstance(payload["key"], str):
        return JSONResponse({"detail": "key must be a string"}, status_code=422)
    await Store().adelete(namespace, payload["key"])
    return Response(status_code=204)


async def store_search(request: Request) -> JSONResponse | Response:
    payload = await _body(request)
    if payload is None:
        return JSONResponse({"detail": "request body must be an object"}, status_code=422)
    labels = payload.get("namespace_prefix")
    namespace = _namespace(request, labels)
    if namespace is None:
        return JSONResponse(
            {"detail": "namespace_prefix must be an array of strings"}, status_code=422
        )
    labels = cast(list[str], labels)
    if error := _namespace_error(labels):
        return error
    limit = payload.get("limit", 10)
    offset = payload.get("offset", 0)
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or isinstance(offset, bool)
        or not isinstance(offset, int)
    ):
        return JSONResponse({"detail": "limit and offset must be integers"}, status_code=422)
    items = await Store().asearch(
        namespace,
        filter=payload.get("filter"),
        limit=limit,
        offset=offset,
        query=payload.get("query"),
        refresh_ttl=payload.get("refresh_ttl"),
    )
    return JSONResponse({"items": [_item(request, item) for item in items]})


async def store_list_namespaces(request: Request) -> JSONResponse | Response:
    payload = await _body(request)
    if payload is None:
        return JSONResponse({"detail": "request body must be an object"}, status_code=422)
    prefix = payload.get("prefix")
    suffix = payload.get("suffix")
    if prefix is not None and (
        not isinstance(prefix, list) or not all(isinstance(item, str) for item in prefix)
    ):
        return JSONResponse({"detail": "prefix must be an array of strings"}, status_code=422)
    if suffix is not None and (
        not isinstance(suffix, list) or not all(isinstance(item, str) for item in suffix)
    ):
        return JSONResponse({"detail": "suffix must be an array of strings"}, status_code=422)
    if prefix and (error := _namespace_error(prefix)):
        return error
    if suffix and (error := _namespace_error(suffix)):
        return error
    scoped_prefix = _namespace(request, prefix) if prefix is not None else _namespace(request, [])
    namespaces = await Store().alist_namespaces(
        prefix=scoped_prefix,
        suffix=tuple(suffix) if suffix is not None else None,
        max_depth=payload.get("max_depth"),
        limit=payload.get("limit", 100),
        offset=payload.get("offset", 0),
    )
    return JSONResponse({"namespaces": [_public_namespace(request, item) for item in namespaces]})


__all__ = ["store_delete", "store_get", "store_list_namespaces", "store_put", "store_search"]
