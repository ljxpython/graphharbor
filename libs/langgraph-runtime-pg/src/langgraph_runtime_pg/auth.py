"""Standard authenticated user handling and optional application scope."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


class RuntimeContextError(ValueError):
    pass


_CORRELATION_FIELDS = ("request_id", "platform_trace_id")
_RUNTIME_CONTEXT_FIELDS = frozenset(
    {
        "accepted_at",
        "permissions",
        "auth_user",
        *_CORRELATION_FIELDS,
    }
)


def _correlation_value(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise RuntimeContextError(f"runtime context field is invalid: {field}")
    return value.strip()


def _runtime_context_secret() -> bytes:
    value = (
        os.environ.get("GRAPHHARBOR_RUNTIME_CONTEXT_SECRET")
        or os.environ.get("GRAPHHARBOR_JWT_SHARED_SECRET")
        or ""
    ).strip()
    if not value:
        raise RuntimeContextError("runtime context signing secret is not configured")
    return value.encode("utf-8")


def _runtime_context_issuer_audience() -> tuple[str | None, str | None]:
    return (
        os.environ.get("GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER"),
        os.environ.get("GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE"),
    )


def _b64_json(value: dict[str, Any]) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode()
    )


def _decode_b64_json(value: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(value + padding))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeContextError("invalid runtime context encoding") from exc
    if not isinstance(decoded, dict):
        raise RuntimeContextError("runtime context payload must be an object")
    return decoded


def _auth_user_mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise RuntimeContextError("custom auth user must be a JSON object")
    result = dict(value)
    try:
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise RuntimeContextError("custom auth user must be JSON-compatible") from exc
    if len(encoded.encode("utf-8")) > 65_536:
        raise RuntimeContextError("custom auth user exceeds 65536 bytes")
    for field in _CORRELATION_FIELDS:
        if field in result:
            result[field] = _correlation_value(result[field], f"custom auth user {field}")
    return result


def _permission_names(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(value.split())
    if not isinstance(value, (list, tuple, set, frozenset)) or any(
        not isinstance(item, str) for item in value
    ):
        raise RuntimeContextError("runtime context permissions are invalid")
    return frozenset(item.strip() for item in value if item.strip())


def sign_runtime_context(
    context: Mapping[str, Any],
    *,
    run_id: str,
    thread_id: str | None,
) -> str:
    """Sign the accepted execution identity so it cannot be rebuilt from run input."""
    now = int(time.time())
    if not isinstance(context, Mapping) or any(not isinstance(key, str) for key in context):
        raise RuntimeContextError("runtime context fields are invalid")
    unknown = set(context) - _RUNTIME_CONTEXT_FIELDS
    if unknown:
        raise RuntimeContextError("runtime context contains unknown fields")
    permissions = sorted(_permission_names(context.get("permissions") or []))
    auth_user = _auth_user_mapping(context.get("auth_user") or {})
    if "identity" not in auth_user:
        raise RuntimeContextError("runtime context auth_user requires identity")
    accepted_at = context.get("accepted_at", now)
    if isinstance(accepted_at, bool) or not isinstance(accepted_at, int) or accepted_at < 0:
        raise RuntimeContextError("runtime context acceptance time is invalid")
    context_values: dict[str, Any] = {
        "accepted_at": accepted_at,
        "permissions": permissions,
        "auth_user": auth_user,
    }
    for field in _CORRELATION_FIELDS:
        value = _correlation_value(context.get(field), field)
        if value is not None:
            context_values[field] = value
    claims: dict[str, Any] = {
        "v": 2,
        "accepted_at": accepted_at,
        "run_id": str(run_id),
        "thread_id": str(thread_id) if thread_id is not None else None,
        "context": context_values,
    }
    issuer, audience = _runtime_context_issuer_audience()
    if bool(issuer) != bool(audience):
        raise RuntimeContextError("runtime context issuer and audience must be configured together")
    if os.environ.get("GRAPHHARBOR_ENV", "development") == "production" and not issuer:
        raise RuntimeContextError("runtime context issuer and audience are required in production")
    if issuer and audience:
        claims.update({"iss": issuer, "aud": audience})
    encoded = _b64_json(claims)
    signature = hmac.new(_runtime_context_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_runtime_context(
    token: str,
    *,
    run_id: str,
    thread_id: str | None,
) -> dict[str, Any]:
    return verify_runtime_context_envelope(
        token,
        run_id=run_id,
        thread_id=thread_id,
    )


def verify_runtime_context_envelope(
    token: str,
    *,
    run_id: str,
    thread_id: str | None,
) -> dict[str, Any]:
    try:
        encoded, signature = str(token).split(".", 1)
    except ValueError as exc:
        raise RuntimeContextError("invalid runtime context token") from exc
    expected = hmac.new(_runtime_context_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise RuntimeContextError("invalid runtime context signature")
    claims = _decode_b64_json(encoded)
    issuer, audience = _runtime_context_issuer_audience()
    requires_standard_claims = (
        bool(issuer or audience) or os.environ.get("GRAPHHARBOR_ENV", "development") == "production"
    )
    expected_claims = {"v", "accepted_at", "run_id", "thread_id", "context"}
    if requires_standard_claims:
        if not issuer or not audience:
            raise RuntimeContextError("runtime context issuer and audience are required")
        expected_claims.update({"iss", "aud"})
    if set(claims) != expected_claims:
        raise RuntimeContextError("runtime context contains unknown claims")
    if requires_standard_claims and (claims.get("iss") != issuer or claims.get("aud") != audience):
        raise RuntimeContextError("runtime context issuer or audience does not match")
    accepted_at = claims.get("accepted_at")
    if (
        claims.get("v") != 2
        or isinstance(accepted_at, bool)
        or not isinstance(accepted_at, int)
        or accepted_at < 0
    ):
        raise RuntimeContextError("runtime context version is unsupported")
    if claims.get("run_id") != str(run_id) or claims.get("thread_id") != (
        str(thread_id) if thread_id is not None else None
    ):
        raise RuntimeContextError("runtime context resource does not match the run")
    context = claims.get("context")
    expected_context_fields = {
        "accepted_at",
        "permissions",
        "auth_user",
    }
    for field in _CORRELATION_FIELDS:
        if isinstance(context, dict) and field in context:
            expected_context_fields.add(field)
    if not isinstance(context, dict) or set(context) != expected_context_fields:
        raise RuntimeContextError("runtime context fields are invalid")
    if context["accepted_at"] != accepted_at:
        raise RuntimeContextError("runtime context acceptance time is invalid")
    if not isinstance(context["permissions"], list) or not all(
        isinstance(item, str) for item in context["permissions"]
    ):
        raise RuntimeContextError("runtime context permissions are invalid")
    for field in _CORRELATION_FIELDS:
        if field in context:
            _correlation_value(context[field], field)
    return dict(context)


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str | None = None
    project_id: str | None = None
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    credential_type: str = "custom_auth"
    jti: str = ""
    claims: dict[str, Any] | None = None
    auth_user: dict[str, Any] | None = None
    request_id: str | None = None
    platform_trace_id: str | None = None

    @property
    def sub(self) -> str:
        return self.subject

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes

    def scope_filter(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (("tenant_id", self.tenant_id), ("project_id", self.project_id))
            if value is not None
        }

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> Principal:
        def claim_text(*names: str) -> str:
            for name in names:
                value = claims.get(name)
                if value is not None and str(value).strip():
                    return str(value)
            return ""

        subject = claim_text("sub")
        tenant_id = claim_text("tenant_id", "tenant") or None
        project_id = claim_text("project_id", "project") or None
        jti = claim_text("jti") or subject
        if not subject:
            raise AuthenticationError("authentication claims require sub")

        def claim_set(*names: str) -> frozenset[str]:
            for name in names:
                value = claims.get(name)
                if isinstance(value, str):
                    return frozenset(item for item in value.split() if item)
                if isinstance(value, (list, tuple, set)):
                    return frozenset(str(item) for item in value)
            return frozenset()

        request_id = _correlation_value(claims.get("request_id"), "request_id")
        platform_trace_id = _correlation_value(claims.get("platform_trace_id"), "platform_trace_id")
        return cls(
            subject=subject,
            tenant_id=tenant_id,
            project_id=project_id,
            roles=claim_set("roles", "role"),
            scopes=claim_set("scope", "scopes"),
            credential_type="delegation",
            jti=jti,
            claims=dict(claims),
            request_id=request_id,
            platform_trace_id=platform_trace_id,
        )

    @classmethod
    def from_auth_user(cls, user: Any) -> Principal:
        """Normalize a ``langgraph_sdk.Auth`` user into the runtime Principal."""

        def value(name: str, default: Any = None) -> Any:
            if isinstance(user, Mapping):
                return user.get(name, default)
            try:
                return user[name]
            except (KeyError, TypeError, AttributeError):
                return getattr(user, name, default)

        subject = str(value("identity", value("sub", "")) or "").strip()
        if not subject:
            raise AuthenticationError("custom auth user must contain identity")
        tenant_raw = value("tenant_id")
        project_raw = value("project_id")
        tenant_id = str(tenant_raw).strip() if tenant_raw is not None and str(tenant_raw).strip() else None
        project_id = str(project_raw).strip() if project_raw is not None and str(project_raw).strip() else None
        raw_roles = value("roles", value("role", []))
        raw_scopes = value("scopes", value("permissions", []))

        def normalize(value_: Any) -> frozenset[str]:
            if isinstance(value_, str):
                return frozenset(item for item in value_.split() if item)
            if isinstance(value_, (list, tuple, set, frozenset)):
                return frozenset(str(item) for item in value_ if str(item).strip())
            return frozenset()

        jti = str(value("jti", value("delegation_id", subject)) or subject).strip()
        try:
            auth_user = _auth_user_mapping(
                user if isinstance(user, Mapping) else {"identity": subject}
            )
        except RuntimeContextError as exc:
            raise AuthenticationError(str(exc)) from exc
        return cls(
            subject=subject,
            tenant_id=tenant_id,
            project_id=project_id,
            roles=normalize(raw_roles),
            scopes=normalize(raw_scopes),
            credential_type=str(value("credential_type", "custom_auth")),
            jti=jti,
            claims=dict(auth_user),
            auth_user=auth_user,
            request_id=_correlation_value(auth_user.get("request_id"), "request_id"),
            platform_trace_id=_correlation_value(
                auth_user.get("platform_trace_id"), "platform_trace_id"
            ),
        )


def principal_from_scope(scope: Scope) -> Principal | None:
    value = scope.get("principal")
    return value if isinstance(value, Principal) else None


def scope_override_error(payload: dict[str, Any], principal: Principal | None) -> str | None:
    """Reject tenant/project values supplied by a client when a Principal exists."""
    if principal is None:
        return None
    for claim, expected in principal.scope_filter().items():
        if claim in payload and payload[claim] != expected:
            return f"{claim} is owned by the authenticated Principal"
    return None


def in_principal_scope(resource: Any, principal: Principal | None) -> bool:
    """Return whether a persisted resource belongs to the request Principal."""
    if principal is None:
        return True
    return all(getattr(resource, key, None) == value for key, value in principal.scope_filter().items())


class PrincipalMiddleware:
    """ASGI middleware; health/discovery are public, all other paths fail closed in prod."""

    def __init__(
        self,
        app: ASGIApp,
        validator: Any = None,  # Deprecated: use auth_handler instead
        *,
        auth_handler: Any | None = None,
        allow_anonymous: bool,
    ) -> None:
        self.app = app
        self.auth_handler = auth_handler
        self.allow_anonymous = allow_anonymous

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in {
            "/ok",
            "/live",
            "/ready",
            "/info",
            "/openapi.json",
            "/metrics",
        }:
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        auth_header = headers.get(b"authorization", b"").decode("latin-1")
        management_header = headers.get(b"x-graphharbor-management-key", b"").decode("latin-1")
        if management_header:
            # Management credentials are deliberately not a data-plane credential.
            # There are no management routes in the Core profile yet, so fail closed.
            await _json_error(
                send, 403, "management credentials cannot access data-plane resources"
            )
            return
        if not auth_header and self.auth_handler is None:
            if self.allow_anonymous:
                await self.app(scope, receive, send)
                return
            await _json_error(send, 401, "missing authorization header")
            return
        try:
            if self.auth_handler is not None:
                user = await authenticate_with_auth_handler(
                    self.auth_handler,
                    scope=scope,
                    receive=receive,
                    authorization=auth_header or None,
                )
                scope["principal"] = Principal.from_auth_user(user)
            elif self.allow_anonymous:
                # No auth_handler and allow_anonymous: proceed without principal
                pass
            else:
                # No auth_handler and not allow_anonymous: must fail
                await _json_error(send, 401, "authentication not configured")
                return
        except AuthorizationError as exc:
            await _json_error(send, 403, str(exc))
            return
        except AuthenticationError as exc:
            await _json_error(send, 401, str(exc))
            return
        await self.app(scope, receive, send)


async def authenticate_with_auth_handler(
    auth_handler: Any,
    *,
    scope: Scope,
    receive: Receive,
    authorization: str | None,
) -> Any:
    """Call the public custom-auth shape without consuming the request body."""
    handler = getattr(auth_handler, "_authenticate_handler", None)
    if not callable(handler):
        if callable(auth_handler):
            handler = auth_handler
        else:
            raise AuthenticationError("configured auth handler has no authenticate function")
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    path_params = scope.get("path_params", {})
    query_string = scope.get("query_string", b"").decode("latin-1")
    query_params = dict(
        item.split("=", 1) if "=" in item else (item, "")
        for item in query_string.split("&")
        if item
    )
    values = {
        "path": scope.get("path", ""),
        "method": scope.get("method", "GET"),
        "headers": headers,
        "authorization": authorization,
        "path_params": path_params,
        "query_params": query_params,
    }
    signature = inspect.signature(handler)
    kwargs = {name: values[name] for name in signature.parameters if name in values}
    try:
        result = handler(**kwargs)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        detail = str(getattr(exc, "detail", exc))
        if status is not None and int(status) < 500:
            if int(status) == 403:
                raise AuthorizationError(detail) from exc
            raise AuthenticationError(detail) from exc
        raise AuthenticationError(detail) from exc
    if result is None or result is False:
        raise AuthenticationError("custom authentication rejected the request")
    return result


async def _json_error(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}, separators=(",", ":")).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})
