"""Platform delegation JWT validation and the shared request Principal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import inspect
import json
import os
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import jwt
from starlette.types import ASGIApp, Receive, Scope, Send


class AuthenticationError(ValueError):
    pass


class AuthorizationError(ValueError):
    pass


class RuntimeContextError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    version: str
    allowed_model_ids: tuple[str, ...]
    allowed_tool_names: tuple[str, ...]


def _policy_name(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 128:
        raise RuntimeContextError(f"runtime policy field is invalid: {field}")
    if not value.isascii() or any(char.isspace() or not char.isprintable() for char in value):
        raise RuntimeContextError(f"runtime policy field is invalid: {field}")
    return value


def _policy_names(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RuntimeContextError(f"runtime policy field is invalid: {field}")
    values = tuple(_policy_name(item, field) for item in value)
    if len(values) != len(set(values)):
        raise RuntimeContextError(f"runtime policy field is invalid: {field}")
    return tuple(sorted(values))


def parse_runtime_policy(value: Mapping[str, Any]) -> RuntimePolicy:
    if set(value) != {"version", "allowed_model_ids", "allowed_tool_names"}:
        raise RuntimeContextError("runtime policy claims are invalid")
    version = value["version"]
    if not isinstance(version, str) or not version.strip() or len(version) > 100_000:
        raise RuntimeContextError("runtime policy field is invalid: policy_version")
    models = _policy_names(value["allowed_model_ids"], "allowed_model_ids")
    if not models:
        raise RuntimeContextError("runtime policy requires allowed_model_ids")
    return RuntimePolicy(
        version=version,
        allowed_model_ids=models,
        allowed_tool_names=_policy_names(value["allowed_tool_names"], "allowed_tool_names"),
    )


def validate_policy_overrides(
    policy: RuntimePolicy | None,
    *,
    configurable: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> None:
    """Reject model/tool overrides outside the signed policy allowlists."""
    if policy is None:
        return
    policy = parse_runtime_policy(_policy_claims(policy))
    sources = tuple(item for item in (configurable, context) if isinstance(item, Mapping))
    for source in sources:
        model_id = source.get("model_id")
        if model_id is not None and model_id not in policy.allowed_model_ids:
            raise RuntimeContextError("runtime policy rejects model_id")
        tools = source.get("tools")
        if tools is None:
            continue
        if not isinstance(tools, (list, tuple)) or any(
            not isinstance(name, str) or name not in policy.allowed_tool_names for name in tools
        ):
            raise RuntimeContextError("runtime policy rejects tool names")


def _policy_claims(policy: RuntimePolicy) -> dict[str, Any]:
    return {
        "version": policy.version,
        "allowed_model_ids": list(policy.allowed_model_ids),
        "allowed_tool_names": list(policy.allowed_tool_names),
    }


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
        os.environ.get("GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER")
        or os.environ.get("GRAPHHARBOR_JWT_ISSUER"),
        os.environ.get("GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE")
        or os.environ.get("GRAPHHARBOR_JWT_AUDIENCE"),
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
    policy: RuntimePolicy | None = None,
    ttl_seconds: int = 300,
) -> str:
    """Sign the worker context so it cannot be rebuilt from run input."""
    now = int(time.time())
    if ttl_seconds < 1:
        raise RuntimeContextError("runtime context TTL must be positive")
    context_values: dict[str, Any] = {
        "user_id": str(context.get("user_id") or ""),
        "tenant_id": str(context.get("tenant_id") or ""),
        "project_id": str(context.get("project_id") or ""),
        "role": str(context.get("role") or ""),
        "permissions": sorted(_permission_names(context.get("permissions") or [])),
    }
    if context.get("auth_user") is not None:
        context_values["auth_user"] = _auth_user_mapping(context["auth_user"])
    claims: dict[str, Any] = {
        "v": 1,
        "iat": now,
        "exp": now + ttl_seconds,
        "run_id": str(run_id),
        "thread_id": str(thread_id) if thread_id is not None else None,
        "context": context_values,
    }
    if policy is not None:
        claims["policy"] = _policy_claims(parse_runtime_policy(_policy_claims(policy)))
    issuer, audience = _runtime_context_issuer_audience()
    if bool(issuer) != bool(audience):
        raise RuntimeContextError("runtime context issuer and audience must be configured together")
    if os.environ.get("GRAPHHARBOR_ENV", "development") == "production" and not issuer:
        raise RuntimeContextError("runtime context issuer and audience are required in production")
    if issuer and audience:
        claims.update({"iss": issuer, "aud": audience})
    if not all(context_values.get(key) for key in ("user_id", "tenant_id", "project_id", "role")):
        raise RuntimeContextError("runtime context requires identity and scope")
    encoded = _b64_json(claims)
    signature = hmac.new(_runtime_context_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def verify_runtime_context(
    token: str,
    *,
    run_id: str,
    thread_id: str | None,
    tenant_id: str | None,
    project_id: str | None,
) -> dict[str, Any]:
    context, _ = verify_runtime_context_envelope(
        token,
        run_id=run_id,
        thread_id=thread_id,
        tenant_id=tenant_id,
        project_id=project_id,
    )
    return context


def verify_runtime_context_envelope(
    token: str,
    *,
    run_id: str,
    thread_id: str | None,
    tenant_id: str | None,
    project_id: str | None,
) -> tuple[dict[str, Any], RuntimePolicy | None]:
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
    expected_claims = {"v", "iat", "exp", "run_id", "thread_id", "context"}
    has_policy = "policy" in claims
    if has_policy:
        expected_claims.add("policy")
    if os.environ.get("GRAPHHARBOR_ENV", "development") == "production" and not has_policy:
        raise RuntimeContextError("signed runtime context policy is required in production")
    if requires_standard_claims:
        if not issuer or not audience:
            raise RuntimeContextError("runtime context issuer and audience are required")
        expected_claims.update({"iss", "aud"})
    if set(claims) != expected_claims:
        raise RuntimeContextError("runtime context contains unknown claims")
    if requires_standard_claims and (claims.get("iss") != issuer or claims.get("aud") != audience):
        raise RuntimeContextError("runtime context issuer or audience does not match")
    now = int(time.time())
    if claims.get("v") != 1 or not isinstance(claims.get("exp"), int) or claims["exp"] <= now:
        raise RuntimeContextError("runtime context is expired or unsupported")
    if claims.get("run_id") != str(run_id) or claims.get("thread_id") != (
        str(thread_id) if thread_id is not None else None
    ):
        raise RuntimeContextError("runtime context resource does not match the run")
    context = claims.get("context")
    expected_context_fields = {
        "user_id",
        "tenant_id",
        "project_id",
        "role",
        "permissions",
    }
    if isinstance(context, dict) and "auth_user" in context:
        expected_context_fields.add("auth_user")
    if not isinstance(context, dict) or set(context) != expected_context_fields:
        raise RuntimeContextError("runtime context fields are invalid")
    if context["tenant_id"] != tenant_id or context["project_id"] != project_id:
        raise RuntimeContextError("runtime context scope does not match the run")
    if not isinstance(context["permissions"], list) or not all(
        isinstance(item, str) for item in context["permissions"]
    ):
        raise RuntimeContextError("runtime context permissions are invalid")
    auth_user = None
    if "auth_user" in context:
        auth_user = _auth_user_mapping(context["auth_user"])
        if auth_user.get("identity") != context["user_id"]:
            raise RuntimeContextError("custom auth user identity does not match runtime context")
        for key in ("tenant_id", "project_id", "role"):
            if key in auth_user and auth_user[key] != context[key]:
                raise RuntimeContextError(f"custom auth user {key} does not match runtime context")
        if "permissions" in auth_user and _permission_names(
            auth_user["permissions"]
        ) != _permission_names(context["permissions"]):
            raise RuntimeContextError("custom auth user permissions do not match runtime context")
    policy = None
    if has_policy:
        raw_policy = claims.get("policy")
        if not isinstance(raw_policy, dict):
            raise RuntimeContextError("runtime context policy is invalid")
        policy = parse_runtime_policy(raw_policy)
    if auth_user is not None:
        policy_fields = {"policy_version", "allowed_model_ids", "allowed_tool_names"}
        supplied_policy_fields = set(auth_user) & policy_fields
        if supplied_policy_fields:
            if supplied_policy_fields != policy_fields or policy is None:
                raise RuntimeContextError("custom auth user policy does not match runtime context")
            auth_policy = parse_runtime_policy(
                {
                    "version": auth_user["policy_version"],
                    "allowed_model_ids": auth_user["allowed_model_ids"],
                    "allowed_tool_names": auth_user["allowed_tool_names"],
                }
            )
            if auth_policy != policy:
                raise RuntimeContextError("custom auth user policy does not match runtime context")
    return dict(context), policy


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    tenant_id: str
    project_id: str
    roles: frozenset[str]
    scopes: frozenset[str]
    credential_type: str
    jti: str
    claims: dict[str, Any]
    policy: RuntimePolicy | None = None
    auth_user: dict[str, Any] | None = None

    @property
    def sub(self) -> str:
        return self.subject

    def can(self, scope: str) -> bool:
        return scope in self.scopes or "*" in self.scopes

    def scope_filter(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "project_id": self.project_id}

    @classmethod
    def from_claims(cls, claims: dict[str, Any]) -> Principal:
        def claim_text(*names: str) -> str:
            for name in names:
                value = claims.get(name)
                if value is not None and str(value).strip():
                    return str(value)
            return ""

        subject = claim_text("sub")
        tenant_id = claim_text("tenant_id", "tenant")
        project_id = claim_text("project_id", "project")
        jti = claim_text("jti")
        if not subject or not tenant_id or not project_id or not jti:
            raise AuthenticationError("delegation JWT requires sub, tenant_id, project_id, and jti")

        def claim_set(*names: str) -> frozenset[str]:
            for name in names:
                value = claims.get(name)
                if isinstance(value, str):
                    return frozenset(item for item in value.split() if item)
                if isinstance(value, (list, tuple, set)):
                    return frozenset(str(item) for item in value)
            return frozenset()

        policy_fields = {"policy_version", "allowed_model_ids", "allowed_tool_names"}
        supplied_policy_fields = set(claims) & policy_fields
        policy = None
        if supplied_policy_fields:
            if supplied_policy_fields != policy_fields:
                raise AuthenticationError("delegation JWT policy claims are incomplete")
            try:
                policy = parse_runtime_policy(
                    {
                        "version": claims["policy_version"],
                        "allowed_model_ids": claims["allowed_model_ids"],
                        "allowed_tool_names": claims["allowed_tool_names"],
                    }
                )
            except RuntimeContextError as exc:
                raise AuthenticationError("delegation JWT policy claims are invalid") from exc
            if (
                claims.get("policy_tenant_id", tenant_id) != tenant_id
                or claims.get("policy_project_id", project_id) != project_id
            ):
                raise AuthenticationError("delegation JWT policy scope is invalid")
        return cls(
            subject=subject,
            tenant_id=tenant_id,
            project_id=project_id,
            roles=claim_set("roles", "role"),
            scopes=claim_set("scope", "scopes"),
            credential_type="delegation",
            jti=jti,
            claims=dict(claims),
            policy=policy,
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
        tenant_id = str(value("tenant_id", "__default") or "__default").strip()
        project_id = str(value("project_id", "__default") or "__default").strip()
        raw_roles = value("roles", value("role", []))
        raw_scopes = value("scopes", value("permissions", []))

        def normalize(value_: Any) -> frozenset[str]:
            if isinstance(value_, str):
                return frozenset(item for item in value_.split() if item)
            if isinstance(value_, (list, tuple, set, frozenset)):
                return frozenset(str(item) for item in value_ if str(item).strip())
            return frozenset()

        jti = str(value("jti", value("delegation_id", subject)) or subject).strip()
        policy = None
        policy_values = [
            value(name) for name in ("policy_version", "allowed_model_ids", "allowed_tool_names")
        ]
        if any(item is not None for item in policy_values):
            if any(item is None for item in policy_values):
                raise AuthenticationError("custom auth policy claims are incomplete")
            try:
                policy = parse_runtime_policy(
                    {
                        "version": policy_values[0],
                        "allowed_model_ids": policy_values[1],
                        "allowed_tool_names": policy_values[2],
                    }
                )
            except RuntimeContextError as exc:
                raise AuthenticationError("custom auth policy claims are invalid") from exc
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
            policy=policy,
            auth_user=auth_user,
        )


class JWKSCache:
    """Tiny TTL cache; unknown ``kid`` forces one refresh for key rotation."""

    def __init__(self, url: str, *, ttl_seconds: int = 300, timeout_seconds: float = 3.0) -> None:
        self.url = url
        self.ttl_seconds = max(ttl_seconds, 1)
        self.timeout_seconds = timeout_seconds
        self._expires_at = 0.0
        self._keys: dict[str, dict[str, Any]] = {}

    def _fetch(self) -> dict[str, dict[str, Any]]:
        with urllib.request.urlopen(self.url, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        keys = payload.get("keys")
        if not isinstance(keys, list):
            raise AuthenticationError("JWKS response does not contain a keys array")
        self._keys = {str(item["kid"]): item for item in keys if item.get("kid")}
        self._expires_at = time.monotonic() + self.ttl_seconds
        return self._keys

    def get(self, kid: str) -> dict[str, Any]:
        if time.monotonic() >= self._expires_at:
            self._fetch()
        key = self._keys.get(kid)
        if key is None:
            key = self._fetch().get(kid)
        if key is None:
            raise AuthenticationError(f"unknown delegation JWT kid: {kid}")
        return key


class DelegationJWTValidator:
    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str | None = None,
        shared_secret: str | None = None,
        algorithms: tuple[str, ...] = ("RS256",),
        leeway_seconds: int = 30,
        jwks_ttl_seconds: int = 300,
        require_policy: bool = False,
    ) -> None:
        if not jwks_url and not shared_secret:
            raise ValueError("either jwks_url or shared_secret is required")
        self.issuer = issuer
        self.audience = audience
        self.jwks = JWKSCache(jwks_url, ttl_seconds=jwks_ttl_seconds) if jwks_url else None
        self.shared_secret = shared_secret
        self.algorithms = algorithms
        self.leeway_seconds = leeway_seconds
        self.require_policy = require_policy

    def validate(self, token: str) -> Principal:
        try:
            header = jwt.get_unverified_header(token)
            algorithm = str(header.get("alg", ""))
            if algorithm not in self.algorithms:
                raise AuthenticationError("delegation JWT algorithm is not allowed")
            if self.shared_secret:
                key: Any = self.shared_secret
            else:
                if self.jwks is None:
                    raise AuthenticationError("JWKS validator is not configured")
                try:
                    jwk = self.jwks.get(str(header["kid"]))
                except (KeyError, TypeError, OSError, ValueError) as exc:
                    raise AuthenticationError(
                        "unable to resolve delegation JWT signing key"
                    ) from exc
                key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self.algorithms),
                issuer=self.issuer,
                audience=self.audience,
                leeway=self.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub", "jti"]},
            )
            if self.require_policy and not {
                "policy_version",
                "allowed_model_ids",
                "allowed_tool_names",
            } <= set(claims):
                raise AuthenticationError("delegation JWT policy claims are required")
            return Principal.from_claims(claims)
        except AuthenticationError:
            raise
        except jwt.PyJWTError as exc:
            raise AuthenticationError("invalid delegation JWT") from exc
        except (KeyError, TypeError, OSError, ValueError) as exc:
            raise AuthenticationError("invalid delegation JWT") from exc

    @classmethod
    def from_env(cls) -> DelegationJWTValidator:
        issuer = os.environ.get("GRAPHHARBOR_JWT_ISSUER")
        audience = os.environ.get("GRAPHHARBOR_JWT_AUDIENCE")
        if not issuer or not audience:
            raise ValueError("GRAPHHARBOR_JWT_ISSUER and GRAPHHARBOR_JWT_AUDIENCE are required")
        algorithms = tuple(
            item.strip()
            for item in os.environ.get("GRAPHHARBOR_JWT_ALGORITHMS", "RS256").split(",")
            if item.strip()
        )
        if not algorithms:
            raise ValueError("GRAPHHARBOR_JWT_ALGORITHMS must contain at least one algorithm")
        return cls(
            issuer=issuer,
            audience=audience,
            jwks_url=os.environ.get("GRAPHHARBOR_JWT_JWKS_URL"),
            shared_secret=os.environ.get("GRAPHHARBOR_JWT_SHARED_SECRET"),
            algorithms=algorithms,
            leeway_seconds=int(os.environ.get("GRAPHHARBOR_JWT_LEEWAY_SECONDS", "30")),
            require_policy=True,
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
    return all(
        getattr(resource, key, None) == value for key, value in principal.scope_filter().items()
    )


class PrincipalMiddleware:
    """ASGI middleware; health/discovery are public, all other paths fail closed in prod."""

    def __init__(
        self,
        app: ASGIApp,
        validator: DelegationJWTValidator | None,
        *,
        auth_handler: Any | None = None,
        allow_anonymous: bool,
    ) -> None:
        self.app = app
        self.validator = validator
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
            await _json_error(send, 401, "missing delegation token")
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
            else:
                if not auth_header.startswith("Bearer ") or self.validator is None:
                    await _json_error(send, 401, "invalid authorization header")
                    return
                scope["principal"] = self.validator.validate(auth_header[7:].strip())
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
