"""Deployment-owned webhook policy and safe terminal snapshots."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import structlog

from langgraph_runtime_pg.auth import verify_runtime_context
from langgraph_runtime_pg.models import (
    AssistantRow,
    RunCallbackDeliveryRow,
    RunRow,
    RuntimeEventRow,
)

logger = structlog.stdlib.get_logger(__name__)
_TEMPLATE = re.compile(r"\$\{\{\s*env\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,127}\Z")
_PRIVATE_KEYS = {"runtime_context", "runtime_context_token"}


@dataclass(frozen=True)
class WebhookSettings:
    disabled: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    url_policy: dict[str, Any] = field(default_factory=dict)
    allowed_fields: frozenset[str] | None = None
    terminal_url: str | None = None
    key_id: str | None = None
    secret: str | None = field(default=None, repr=False)
    projector: Callable[[BaseException], Mapping[str, Any]] | None = field(default=None, repr=False)

    def validate_url(self, url: Any, *, managed: bool = False) -> str:
        policy = self.url_policy
        if (
            not isinstance(url, str)
            or not url
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or len(url) > int(policy.get("max_url_length", 4096))
        ):
            raise ValueError("invalid webhook URL")
        if url.startswith("/"):
            if url.startswith("//") or managed or policy.get("disable_loopback", True):
                raise ValueError("loopback webhook URL is disabled")
            return url
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or (managed and parsed.query)
        ):
            raise ValueError("invalid webhook URL")
        if (
            (managed or policy.get("require_https", False))
            and parsed.scheme != "https"
            and not (managed and os.environ.get("GRAPHHARBOR_WEBHOOK_ALLOW_HTTP") == "1")
        ):
            raise ValueError("webhook URL must use HTTPS")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        ports = policy.get("allowed_ports")
        if ports and port not in ports:
            raise ValueError("webhook port is not allowed")
        host = parsed.hostname.lower()
        domains = policy.get("allowed_domains") or []
        if domains and not any(
            host == pattern.lower()
            or (pattern.startswith("*.") and host.endswith(pattern[1:].lower()))
            for pattern in domains
        ):
            raise ValueError("webhook domain is not allowed")
        return url

    def validate_ip(self, host: str, *, managed: bool = False) -> None:
        address = ipaddress.ip_address(host)
        if address.is_unspecified or address.is_multicast or host == "169.254.169.254":
            raise ValueError("webhook address is blocked")
        if managed:
            return  # Only the operator can configure this fixed destination.
        if self.url_policy.get("disable_loopback", True) and address.is_loopback:
            raise ValueError("loopback webhook address is blocked")
        if self.url_policy.get("disable_private_ips", True) and not address.is_global:
            raise ValueError("private webhook address is blocked")


_settings = WebhookSettings()


def settings() -> WebhookSettings:
    return _settings


def configure_webhooks(
    config: Mapping[str, Any], projector: Callable | None = None
) -> WebhookSettings:
    global _settings
    raw = config.get("webhooks") or {}
    if not isinstance(raw, dict):
        raise ValueError("webhooks must be an object")
    prefix = raw.get("env_prefix", "LG_WEBHOOK_")
    if not isinstance(prefix, str) or not prefix:
        raise ValueError("webhook env_prefix must not be empty")

    def expand(match: re.Match[str]) -> str:
        name = match[1]
        if not name.startswith(prefix) or not os.environ.get(name):
            raise ValueError(f"webhook header environment variable is unavailable: {name}")
        return os.environ[name]

    headers = raw.get("headers") or {}
    if not isinstance(headers, dict):
        raise ValueError("webhook headers must be an object")
    evaluated = {}
    for key, value in headers.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
            or not isinstance(value, str)
        ):
            raise ValueError("invalid webhook header")
        if key.lower() in {
            "host",
            "content-length",
            "transfer-encoding",
            "content-type",
        } or key.lower().startswith("x-run-completion-"):
            raise ValueError("reserved webhook header")
        value = _TEMPLATE.sub(expand, value)
        if "\r" in value or "\n" in value:
            raise ValueError("invalid webhook header value")
        evaluated[key] = value
    allowed = raw.get("allowed_fields")
    if allowed is not None and (
        not isinstance(allowed, list) or any(not isinstance(v, str) for v in allowed)
    ):
        raise ValueError("webhook allowed_fields must be a list of strings")
    policy = raw.get("url") or {}
    if not isinstance(policy, dict):
        raise ValueError("webhook URL policy must be an object")
    if "max_url_length" in policy and (
        type(policy["max_url_length"]) is not int or not 1 <= policy["max_url_length"] <= 65536
    ):
        raise ValueError("invalid webhook maximum URL length")
    for flag in ("disable_loopback", "disable_private_ips", "require_https"):
        if flag in policy and type(policy[flag]) is not bool:
            raise ValueError("webhook URL policy flags must be booleans")
    if "allowed_ports" in policy and (
        not isinstance(policy["allowed_ports"], list)
        or any(type(port) is not int or not 1 <= port <= 65535 for port in policy["allowed_ports"])
    ):
        raise ValueError("invalid webhook allowed ports")
    if "allowed_domains" in policy and (
        not isinstance(policy["allowed_domains"], list)
        or any(not isinstance(host, str) or not host for host in policy["allowed_domains"])
    ):
        raise ValueError("invalid webhook allowed domains")
    terminal = raw.get("terminal") or {}
    if not isinstance(terminal, dict):
        raise ValueError("webhook terminal profile must be an object")
    enabled = terminal.get("enabled", os.environ.get("GRAPHHARBOR_TERMINAL_WEBHOOK_ENABLED") == "1")
    if type(enabled) is not bool:
        raise ValueError("terminal webhook enabled must be a boolean")
    target = terminal.get("url") or os.environ.get("GRAPHHARBOR_TERMINAL_WEBHOOK_URL")
    key_id = os.environ.get("GRAPHHARBOR_TERMINAL_WEBHOOK_KEY_ID")
    secret = os.environ.get("GRAPHHARBOR_TERMINAL_WEBHOOK_SECRET")
    if enabled and (not target or not key_id or not secret or len(secret.encode()) < 32):
        raise ValueError(
            "terminal webhook URL, key ID and secret (at least 32 characters) are required"
        )
    if enabled and (
        not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key_id)
    ):
        raise ValueError("terminal webhook key ID is invalid")
    if projector is not None and not callable(projector):
        raise ValueError("terminal webhook projector must be callable")
    value = WebhookSettings(
        disabled=bool((config.get("http") or {}).get("disable_webhooks", False)),
        headers=evaluated,
        url_policy=policy,
        allowed_fields=frozenset(allowed) if allowed is not None else None,
        terminal_url=target if enabled else None,
        key_id=key_id,
        secret=secret,
        projector=projector,
    )
    if value.terminal_url:
        value.validate_url(value.terminal_url, managed=True)
    _settings = value
    return value


def public_kwargs(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in _PRIVATE_KEYS and not key.startswith("__graphharbor_")
    }


def safe_projection(result: Mapping[str, Any]) -> dict[str, str | None]:
    if len(result) > 16:
        raise ValueError("terminal projection has too many fields")
    return {
        key: value
        for key, value in result.items()
        if isinstance(key, str)
        and _CODE.fullmatch(key)
        and (value is None or (isinstance(value, str) and _CODE.fullmatch(value)))
    }


def project_error(exc: BaseException) -> dict[str, str | None]:
    if _settings.projector is None:
        return {}
    try:
        result = _settings.projector(exc)
        return safe_projection(result)
    except Exception:
        logger.warning("terminal webhook projector failed")
        return {}


def _encode(value: dict[str, Any]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


async def snapshot_deliveries(
    session: Any, run: RunRow, event: RuntimeEventRow, *, awaiting_stop: bool = False
) -> None:
    cfg = _settings
    if run.lease_owner is not None or run.status not in {
        "success",
        "error",
        "timeout",
        "interrupted",
    }:
        return
    now = datetime.now(UTC)
    if event.payload.get("execution_stopped") is True:
        from sqlalchemy import select

        waiting = await session.scalars(
            select(RunCallbackDeliveryRow).where(
                RunCallbackDeliveryRow.run_id == run.run_id,
                RunCallbackDeliveryRow.state == "awaiting_stop",
            )
        )
        for row in waiting:
            if row.profile == "terminal":
                body = json.loads(row.body)
                row.body = _encode({**body, "execution_stopped": True})
            row.state = "pending"
            row.next_attempt_at = now
            row.retry_deadline = now + timedelta(hours=24)
        await session.flush()
    if cfg.disabled or (event.payload.get("execution_stopped") is False and not awaiting_stop):
        return
    if (
        run.reason in {"cancel_requested", "rollback"}
        and event.payload.get("execution_stopped") is not True
        and not awaiting_stop
    ):
        return
    profiles: list[tuple[str, str, dict[str, Any]]] = []
    if cfg.terminal_url:
        try:
            trusted = verify_runtime_context(
                run.kwargs.get("runtime_context_token", ""),
                run_id=str(run.run_id),
                thread_id=str(run.thread_id) if run.thread_id else None,
            )
            context = trusted["auth_user"].get("callback_context")
        except ValueError:
            context = None
        if isinstance(context, dict) and isinstance(context.get("origin_ref"), str):
            assistant = await session.get(AssistantRow, run.assistant_id)
            graph_id = event.payload.get("graph_name") or (
                assistant.graph_id if assistant else str(run.assistant_id)
            )
            profiles.append(
                (
                    "terminal",
                    cfg.terminal_url,
                    {
                        "schema_version": 1,
                        "event_id": str(event.event_id),
                        "event_type": "run.terminal",
                        "origin_ref": context["origin_ref"],
                        "run_id": str(run.run_id),
                        "thread_id": str(run.thread_id) if run.thread_id else None,
                        "graph_id": graph_id,
                        "status": run.status,
                        "reason": run.reason,
                        "outcome": safe_projection(event.payload.get("outcome") or {}),
                        "execution_stopped": not awaiting_stop,
                        "sequence": event.sequence,
                        "occurred_at": event.created_at.isoformat(),
                    },
                )
            )
    if target := run.kwargs.get("webhook"):
        cfg.validate_url(target)
        body = {
            "run_id": str(run.run_id),
            "thread_id": str(run.thread_id) if run.thread_id else None,
            "assistant_id": str(run.assistant_id),
            "status": run.status,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
            "run_started_at": (run.kwargs.get("__graphharbor_run_budget") or {}).get("started_at"),
            "run_ended_at": now.isoformat(),
            "webhook_sent_at": now.isoformat(),
            "metadata": run.metadata_,
            "kwargs": public_kwargs(run.kwargs),
            "multitask_strategy": run.multitask_strategy or "enqueue",
            "values": event.payload.get("output"),
        }
        if event.payload.get("error"):
            error = event.payload["error"]
            body["error"] = {"error": error.get("type"), "message": error.get("message")}
        if cfg.allowed_fields is not None:
            body = {key: value for key, value in body.items() if key in cfg.allowed_fields}
        profiles.append(("standard", target, body))
    for profile, target, body in profiles:
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(RunCallbackDeliveryRow)
        statement = statement.values(
            event_id=event.event_id,
            run_id=run.run_id,
            thread_id=run.thread_id,
            profile=profile,
            target=target,
            body=_encode(body),
            state="awaiting_stop" if awaiting_stop else "pending",
            attempts=0,
            generation=0,
            next_attempt_at=now,
            created_at=now,
            retry_deadline=now + timedelta(hours=24),
        )
        if awaiting_stop:
            statement = statement.on_conflict_do_nothing(index_elements=["run_id", "profile"])
        else:
            statement = statement.on_conflict_do_update(
                index_elements=["run_id", "profile"],
                set_={
                    "body": statement.excluded.body,
                    "state": "pending",
                    "next_attempt_at": now,
                    "retry_deadline": now + timedelta(hours=24),
                },
                where=RunCallbackDeliveryRow.state == "awaiting_stop",
            )
        await session.execute(statement)
