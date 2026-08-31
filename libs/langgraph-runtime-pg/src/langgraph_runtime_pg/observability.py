"""Small trace-metadata helpers for durable events and logs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, cast

_TRACE_CONTEXT_KEYS = (
    "run_id",
    "thread_id",
    "assistant_id",
    "assistant_version",
    "deployment_version",
    "tenant_id",
    "project_id",
    "user_id",
    "graph_id",
    "model_id",
    "policy_version",
)

_SUMMARY_KEYS = ("data", "input", "output", "error", "interrupts", "content", "prompt", "response")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _jsonable(asdict(cast(Any, value)))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump(mode="json"))
        except TypeError:
            return _jsonable(model_dump())
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return str(value)


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def summarize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return {"kind": "str", "length": len(value), "sha256": _stable_hash(value)}
    if isinstance(value, Mapping):
        keys = [str(key) for key in list(value.keys())[:10]]
        return {
            "kind": "dict",
            "size": len(value),
            "keys": keys,
            "sha256": _stable_hash(value),
        }
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        return {
            "kind": "list",
            "size": len(items),
            "sha256": _stable_hash(items),
        }
    return {"kind": type(value).__name__, "sha256": _stable_hash(value)}


def build_trace_metadata(
    *,
    event: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    trace: dict[str, Any] = {"schema_version": 1}
    if context:
        for key in _TRACE_CONTEXT_KEYS:
            value = context.get(key)
            if value is not None and value != "":
                trace[key] = str(value)
        tool_names = context.get("tool_names")
        if tool_names:
            trace["tool_names"] = summarize_value(tool_names)
    if event:
        trace["event"] = str(event.get("event") or event.get("method") or "custom")
        namespace = event.get("namespace")
        if namespace is not None:
            trace["namespace"] = summarize_value(namespace)
        for key in _SUMMARY_KEYS:
            if key in event:
                trace[key] = summarize_value(event[key])
        if "status" in event:
            trace["status"] = str(event["status"])
        if "reason" in event:
            trace["reason"] = str(event["reason"])
        if "topic" in event:
            trace["topic"] = str(event["topic"])
    return trace


__all__ = ["build_trace_metadata", "summarize_value"]
