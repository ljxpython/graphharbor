"""Small, dependency-light pieces of the public Agent Server contract.

The wire contract is intentionally defined without importing ``langgraph_api``.  HTTP
adapters can use these constants while the compatibility spike remains optional.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from importlib.metadata import PackageNotFoundError, version
from time import time
from typing import Any


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    INTERRUPTED = "interrupted"


PUBLIC_RUN_STATUSES = frozenset(item.value for item in RunStatus)
TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCESS.value,
        RunStatus.ERROR.value,
        RunStatus.TIMEOUT.value,
        RunStatus.INTERRUPTED.value,
    }
)


class RunReason(StrEnum):
    COMPLETED = "completed"
    BUSINESS_ERROR = "business_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    RETRY = "retry"
    CANCEL_REQUESTED = "cancel_requested"
    HITL_INTERRUPT = "hitl_interrupt"
    SHUTDOWN_REQUEUE = "shutdown_requeue"
    TIMEOUT = "timeout"
    ROLLBACK = "rollback"


class ProtocolError(ValueError):
    """Raised when an adapter receives a request outside the frozen contract."""


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    profile: str
    available: bool
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "name": self.name,
            "profile": self.profile,
            "available": self.available,
        }
        if self.reason:
            value["reason"] = self.reason
        return value


CORE_CAPABILITIES = (
    Capability("assistants", "core", True),
    Capability("threads", "core", True),
    Capability("runs", "core", True),
    Capability("cron", "core", True),
    Capability("stream_v2", "core", True),
    Capability("events_v2", "core", True),
    Capability("events_v3", "core", True),
    Capability("store", "extended", True),
)


def ensure_status(value: str | RunStatus) -> RunStatus:
    try:
        return value if isinstance(value, RunStatus) else RunStatus(value)
    except ValueError as exc:
        raise ProtocolError(
            f"Unsupported run status {value!r}; expected one of {sorted(PUBLIC_RUN_STATUSES)}"
        ) from exc


def capability_document() -> dict[str, Any]:
    return {
        "protocol": "langgraph-agent-server",
        "protocol_version": "v2",
        "implementation_profile": "core-resource-foundation",
        "run_statuses": sorted(PUBLIC_RUN_STATUSES),
        "capabilities": [item.as_dict() for item in CORE_CAPABILITIES],
        "authentication": {"production": "platform-api-delegation-jwt"},
    }


def official_info_document() -> dict[str, Any]:
    """Return the public ``langgraph dev`` info shape.

    The API version is injected by the compatibility workflow for releases;
    the installed compatibility package is only a local fallback.
    """
    try:
        langgraph_version = version("langgraph")
    except PackageNotFoundError:
        langgraph_version = "unknown"
    try:
        api_version = version("langgraph-api")
    except PackageNotFoundError:
        api_version = os.environ.get("GRAPHHARBOR_OFFICIAL_AGENT_SERVER_VERSION", "0.13.0")
    return {
        "flags": {
            "assistants": True,
            "crons": True,
            "langsmith": False,
            "langsmith_tracing_replicas": True,
            "langsmith_tracing_session_on_runs": True,
        },
        "host": {
            "host_revision_id": None,
            "kind": "self-hosted",
            "project_id": None,
            "revision_id": None,
            "tenant_id": None,
        },
        "langgraph_py_version": langgraph_version,
        "version": os.environ.get("GRAPHHARBOR_OFFICIAL_AGENT_SERVER_VERSION", api_version),
    }


def project_v3_event(
    event: dict[str, Any],
    *,
    sequence: int | None = None,
    fallback_namespace: list[str] | None = None,
) -> dict[str, Any]:
    """Normalize a LangGraph v3 event into the public typed envelope.

    Worker events are persisted in a compact internal shape while LangGraph's
    public v3 protocol nests the payload under ``params``. Accept both shapes
    here so HTTP, replay and live fan-out cannot drift apart.
    """
    raw_params = event.get("params")
    params = raw_params if isinstance(raw_params, dict) else {}
    method = str(event.get("method") or event.get("event") or params.get("method") or "custom")
    raw_namespace = params.get("namespace", event.get("namespace", fallback_namespace or []))
    namespace = (
        [str(item) for item in raw_namespace] if isinstance(raw_namespace, (list, tuple)) else []
    )
    data = params["data"] if "data" in params else event.get("data")
    if method == "lifecycle" and data is None and event.get("status"):
        status = str(event["status"])
        phase = {
            RunStatus.RUNNING.value: "running",
            RunStatus.SUCCESS.value: "completed",
            RunStatus.ERROR.value: "failed",
            RunStatus.TIMEOUT.value: "failed",
            RunStatus.INTERRUPTED.value: "interrupted",
        }.get(status, status)
        data = {"event": phase, "status": status}
        for key in ("graph_name", "cause", "reason", "output", "error", "interrupts"):
            if key in event:
                data[key] = event[key]
    if method == "input.requested":
        method = "input"
        if isinstance(data, dict) and "event" not in data:
            data = {"event": "requested", **data}
    timestamp = params.get("timestamp", event.get("timestamp"))
    try:
        timestamp_value = int(str(timestamp))
    except (TypeError, ValueError):
        timestamp_value = int(time() * 1000)
    projected: dict[str, Any] = {
        "method": method,
        "params": {
            "namespace": namespace,
            "timestamp": timestamp_value,
            "data": data,
        },
    }
    interrupts = params.get("interrupts", event.get("interrupts"))
    if interrupts:
        projected["params"]["interrupts"] = interrupts
    event_sequence = sequence if sequence is not None else event.get("seq")
    if event_sequence is not None:
        try:
            projected["seq"] = int(event_sequence)
        except (TypeError, ValueError):
            pass
    return projected


def protocol_event(
    *,
    event_id: str,
    sequence: int,
    run_id: str,
    thread_id: str,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Project a worker event into the public thread protocol envelope."""
    name = str(event.get("event") or event.get("method") or "custom")
    namespace = [str(item) for item in (event.get("namespace") or [])]
    data = event.get("data")
    if name == "lifecycle":
        status = str(event.get("status") or "")
        phase = {
            RunStatus.RUNNING.value: "started",
            RunStatus.SUCCESS.value: "completed",
            RunStatus.ERROR.value: "failed",
            RunStatus.TIMEOUT.value: "failed",
            RunStatus.INTERRUPTED.value: "interrupted",
        }.get(status, status or "running")
        data = {"event": phase, "status": status}
        if event.get("reason"):
            data["reason"] = event["reason"]
        if "output" in event:
            data["output"] = event["output"]
        if "error" in event:
            data["error"] = event["error"]
        if event.get("interrupts"):
            data["interrupts"] = event["interrupts"]
        name = "lifecycle"
    elif name == "input.requested":
        name = "input.requested"
    return {
        "event_id": event_id,
        "seq": sequence,
        "method": name,
        "params": {
            "namespace": namespace,
            "timestamp": int(time() * 1000),
            "data": data,
            "run_id": run_id,
            "thread_id": thread_id,
            "interrupts": event.get("interrupts") or [],
        },
    }
