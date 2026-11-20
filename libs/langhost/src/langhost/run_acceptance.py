"""Strict HTTP boundary for GraphHarbor acceptance receipts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from langgraph_runtime_pg.auth import principal_from_scope
from langgraph_runtime_pg.authorization import authorize
from langgraph_runtime_pg.database import connect
from langgraph_runtime_pg.run_acceptance import (
    DIGEST,
    AcceptanceGrant,
    RunAcceptanceRepository,
    request_digest,
)


def _header(request: Request, name: str) -> str:
    values = request.headers.getlist(name)
    if len(values) != 1:
        raise HTTPException(422, f"exactly one {name} header is required")
    return values[0]


def acceptance_grant(request: Request) -> AcceptanceGrant:
    principal = principal_from_scope(request.scope)
    try:
        grant = AcceptanceGrant.parse(
            (principal.auth_user or {}).get("run_acceptance") if principal else None
        )
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    key = _header(request, "Idempotency-Key")
    if not 1 <= len(key) <= 256 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise HTTPException(422, "Idempotency-Key must be 1..256 visible ASCII characters")
    digest = _header(request, "GraphHarbor-Request-Digest")
    if not DIGEST.fullmatch(digest):
        raise HTTPException(422, "invalid GraphHarbor-Request-Digest")
    if (
        str(grant.thread_id) != request.path_params["thread_id"]
        or grant.key_sha256 != hashlib.sha256(key.encode("ascii")).hexdigest()
        or grant.request_digest != digest
    ):
        raise HTTPException(403, "request does not match authenticated acceptance target")
    if request.query_params:
        raise HTTPException(422, "acceptance endpoint does not accept query parameters")
    return grant


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


async def parse_acceptance_request(request: Request, grant: AcceptanceGrant) -> dict[str, Any]:
    if grant.operation != "create":
        raise HTTPException(403, "read grant cannot create a Run")
    content_type = _header(request, "Content-Type").lower().replace(" ", "")
    if content_type not in {
        "application/json",
        "application/json;charset=utf-8",
    } or request.headers.get("content-encoding"):
        raise HTTPException(422, "acceptance body must be uncompressed UTF-8 application/json")
    raw = await request.body()
    if request_digest(raw) != grant.request_digest:
        raise HTTPException(422, "request body digest does not match")
    try:
        payload = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object,
            parse_constant=_constant,
            parse_float=_float,
        )
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "invalid acceptance JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(422, "acceptance body must be a JSON object")
    forbidden = {
        "idempotency_key",
        "runtime_context",
        "runtime_context_token",
        "__graphharbor_runtime_context",
        "run_acceptance_receipt",
    }
    if forbidden.intersection(payload) or any(key.startswith("__graphharbor_") for key in payload):
        raise HTTPException(422, "acceptance body contains private control fields")
    if payload.get("if_not_exists") == "create":
        raise HTTPException(422, "acceptance requires an existing Thread")
    return payload


def receipt_response(value: dict[str, Any]) -> JSONResponse:
    return JSONResponse(value, headers={"Cache-Control": "no-store"})


async def create_acceptance(request: Request) -> JSONResponse:
    from langhost.core_api import runs_create

    grant = acceptance_grant(request)
    payload = await parse_acceptance_request(request, grant)
    return await runs_create(
        request, thread_value=str(grant.thread_id), payload=payload, acceptance=grant
    )


async def get_acceptance(request: Request) -> JSONResponse:
    grant = acceptance_grant(request)
    if await request.body():
        raise HTTPException(422, "acceptance GET does not accept a body")
    principal = principal_from_scope(request.scope)
    filters = await authorize(
        request.app.state.auth_handler,
        principal.auth_user if principal else None,
        "threads",
        "read",
        grant.authorization_value(),
        require_handler=True,
    )
    repo = RunAcceptanceRepository()
    async with connect() as conn:
        value = await repo.response(
            conn.session, grant, await repo.get(conn.session, grant), filters
        )
    return receipt_response(value)


def openapi_acceptance() -> dict[str, Any]:
    headers = [
        {"name": name, "in": "header", "required": True, "schema": schema}
        for name, schema in (
            (
                "Idempotency-Key",
                {"type": "string", "minLength": 1, "maxLength": 256, "pattern": "^[!-~]+$"},
            ),
            ("GraphHarbor-Request-Digest", {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}),
        )
    ]
    fields: dict[str, Any] = {
        "schema_version": {"const": 1},
        "result": {"enum": ["accepted", "definitively_not_accepted", "unknown"]},
        "thread_id": {"type": "string", "format": "uuid"},
        "key_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "request_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "run_id": {"type": ["string", "null"], "format": "uuid"},
        "accepted_at": {"type": ["string", "null"], "format": "date-time"},
        "observed_at": {"type": "string", "format": "date-time"},
        "details_expires_at": {"type": ["string", "null"], "format": "date-time"},
        "reason": {"type": ["string", "null"]},
        "run_state": {
            "type": "object",
            "required": ["available", "status"],
            "additionalProperties": False,
            "properties": {
                "available": {"type": "boolean"},
                "status": {"type": ["string", "null"]},
            },
        },
    }
    response = {
        "description": "Committed acceptance fact; unknown is never negative evidence",
        "headers": {
            "Cache-Control": {"schema": {"const": "no-store"}},
        },
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": list(fields),
                    "additionalProperties": False,
                    "properties": fields,
                }
            }
        },
    }
    operations = {}
    for method in ("get", "post"):
        operations[method] = {
            "description": "GraphHarbor extension. Requires an authenticated run_acceptance grant binding scope_id, credential_id, operation, thread_id, key_sha256 and request_digest, plus an explicit SDK Auth handler. GET only reads committed facts; POST binds raw UTF-8 JSON bytes and replays the original receipt. No implicit Thread creation or fallback to ordinary POST.",
            "parameters": [
                {
                    "name": "thread_id",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "format": "uuid"},
                },
                *headers,
            ],
            "responses": {
                "200": response,
                **{
                    str(code): {"description": detail}
                    for code, detail in (
                        (401, "Authentication rejected"),
                        (403, "Missing or mismatched fixed grant/handler"),
                        (404, "Authorization metadata filter excluded receipt"),
                        (409, "Key bound to another request or expired for POST"),
                        (422, "Invalid headers/body/digest/query"),
                        (503, "Acceptance integrity or transaction unavailable; outcome unknown"),
                    )
                },
            },
        }
    operations["post"]["requestBody"] = {
        "required": True,
        "content": {
            "application/json": {"schema": {"type": "object", "required": ["assistant_id"]}}
        },
    }
    return operations
