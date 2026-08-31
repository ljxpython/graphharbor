"""Postgres+Redis LangGraph runtime (``LANGGRAPH_RUNTIME_EDITION=pg``).

The package entry point stays dependency-light.  Production graph discovery only
needs ``graph_registry`` and must not import the legacy compatibility modules (or
their optional ``langgraph-api``/migration dependencies) as a side effect.
"""

from importlib import import_module
from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("graphharbor-runtime")
except PackageNotFoundError:  # pragma: no cover - editable / source tree edge
    __version__ = "0.0.0"
_MODULES = {
    "auth",
    "checkpoint",
    "database",
    "deepagent_workspace",
    "lifespan",
    "metrics",
    "migrate",
    "models",
    "ops",
    "production",
    "protocol",
    "queue",
    "redis_stream",
    "retry",
    "routes",
    "run_state",
    "run_store",
    "store",
}


def __getattr__(name: str) -> Any:
    if name in _MODULES:
        module = import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["__version__", *_MODULES]
