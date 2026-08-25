"""Platform delegation JWT validation and the shared request Principal."""

from __future__ import annotations

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

        return cls(
            subject=subject,
            tenant_id=tenant_id,
            project_id=project_id,
            roles=claim_set("roles", "role"),
            scopes=claim_set("scope", "scopes"),
            credential_type="delegation",
            jti=jti,
            claims=dict(claims),
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
        return cls(
            subject=subject,
            tenant_id=tenant_id,
            project_id=project_id,
            roles=normalize(raw_roles),
            scopes=normalize(raw_scopes),
            credential_type=str(value("credential_type", "custom_auth")),
            jti=jti,
            claims=dict(user) if isinstance(user, Mapping) else {"identity": subject},
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
    ) -> None:
        if not jwks_url and not shared_secret:
            raise ValueError("either jwks_url or shared_secret is required")
        self.issuer = issuer
        self.audience = audience
        self.jwks = JWKSCache(jwks_url, ttl_seconds=jwks_ttl_seconds) if jwks_url else None
        self.shared_secret = shared_secret
        self.algorithms = algorithms
        self.leeway_seconds = leeway_seconds

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
