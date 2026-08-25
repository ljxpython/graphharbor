"""Small dependency-free metrics registry for self-hosted deployments."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping
from threading import Lock

_lock = Lock()
_counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], int] = defaultdict(int)
_gauges: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
_NAME = re.compile(r"[^a-zA-Z0-9_:]")


def _key(name: str, labels: Mapping[str, object] | None) -> tuple[str, tuple[tuple[str, str], ...]]:
    metric = _NAME.sub("_", name)
    pairs = tuple(sorted((str(k), str(v)) for k, v in (labels or {}).items()))
    return metric, pairs


def inc(name: str, value: int = 1, *, labels: Mapping[str, object] | None = None) -> None:
    key = _key(name, labels)
    with _lock:
        _counters[key] += int(value)


def set_gauge(name: str, value: int, *, labels: Mapping[str, object] | None = None) -> None:
    key = _key(name, labels)
    with _lock:
        _gauges[key] = int(value)


def get_metrics() -> dict[str, dict[str, int]]:
    """Return a JSON-friendly snapshot grouped by metric name."""
    with _lock:
        values = {**_counters, **_gauges}
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for (name, labels), value in values.items():
        suffix = ",".join(f"{key}={val}" for key, val in labels)
        result[name][suffix] = value
    return dict(result)


def prometheus_text() -> str:
    with _lock:
        values = [(False, *item) for item in _counters.items()] + [
            (True, *item) for item in _gauges.items()
        ]
    lines: list[str] = []
    for _, (name, labels), value in sorted(values, key=lambda item: item[1][0]):
        label_text = ""
        if labels:
            escaped = ((key, val.replace("\\", "\\\\").replace('"', '\\"')) for key, val in labels)
            label_text = "{" + ",".join(f'{key}="{val}"' for key, val in escaped) + "}"
        lines.append(f"{name}{label_text} {value}")
    return "\n".join(lines) + ("\n" if lines else "")


def reset_metrics() -> None:
    with _lock:
        _counters.clear()
        _gauges.clear()


__all__ = ["get_metrics", "inc", "prometheus_text", "reset_metrics", "set_gauge"]
