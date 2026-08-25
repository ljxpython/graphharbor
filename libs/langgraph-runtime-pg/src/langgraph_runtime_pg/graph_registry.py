"""Load the public ``langgraph.json`` graph registry without private APIs."""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GraphSpec:
    graph_id: str
    source: str


def resolve_config_base_dir(path: pathlib.Path, config: dict[str, Any]) -> pathlib.Path:
    """Resolve the project root used by relative graph/custom-app paths."""
    base_dir = path.parent
    graphs = config.get("graphs") or {}
    if not isinstance(graphs, dict):
        return base_dir
    sources = [str(raw if isinstance(raw, str) else raw.get("path", "")) for raw in graphs.values()]
    if sources and not any((base_dir / source.partition(":")[0]).is_file() for source in sources):
        parent = base_dir.parent
        if any((parent / source.partition(":")[0]).is_file() for source in sources):
            return parent
    return base_dir


def _load_symbol(source: str, base_dir: pathlib.Path) -> Any:
    path_text, separator, symbol = source.partition(":")
    if not separator or not symbol:
        raise ValueError(f"invalid graph source {source!r}; expected path.py:symbol")
    path = (base_dir / path_text).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"graph source does not exist: {path}")
    module_name = f"graphharbor_graph_{path.stem}_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load graph module {path}")
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    graph = getattr(module, symbol)
    if callable(graph) and not hasattr(graph, "ainvoke"):
        graph = graph()
    if not hasattr(graph, "ainvoke") or not hasattr(graph, "astream_events"):
        raise TypeError(f"graph {source!r} is not a compiled LangGraph")
    return graph


class GraphRegistry:
    """Immutable graph registry shared by API discovery and workers."""

    def __init__(self, graphs: dict[str, Any], *, base_dir: pathlib.Path) -> None:
        self.base_dir = base_dir.resolve()
        self._graphs: dict[str, Any] = {}
        for graph_id, raw in graphs.items():
            source = raw if isinstance(raw, str) else raw.get("path")
            if not source:
                raise ValueError(f"graph {graph_id!r} has no path")
            self._graphs[str(graph_id)] = _load_symbol(str(source), self.base_dir)

    @classmethod
    def from_config(cls, config: dict[str, Any], *, base_dir: pathlib.Path) -> GraphRegistry:
        graphs = config.get("graphs") or {}
        if not isinstance(graphs, dict):
            raise ValueError("langgraph.json 'graphs' must be an object")
        return cls(graphs, base_dir=base_dir)

    @classmethod
    def from_path(cls, path: pathlib.Path) -> GraphRegistry:
        config = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("langgraph.json must contain a JSON object")
        # LangGraph projects commonly keep the config under a package directory
        # while graph paths remain relative to the project root. Use the config
        # directory first, then the parent when that is the only valid layout.
        base_dir = resolve_config_base_dir(path, config)
        return cls.from_config(config, base_dir=base_dir)

    def get(self, graph_id: str) -> Any:
        try:
            return self._graphs[graph_id]
        except KeyError as exc:
            raise KeyError(f"unknown graph {graph_id!r}") from exc

    def attach_checkpointer(self, checkpointer: Any) -> None:
        """Attach the process-owned public saver to compiled graphs."""
        for graph in self._graphs.values():
            if hasattr(graph, "checkpointer"):
                graph.checkpointer = checkpointer

    def ids(self) -> tuple[str, ...]:
        return tuple(self._graphs)

    def __len__(self) -> int:
        return len(self._graphs)


__all__ = ["GraphRegistry", "GraphSpec", "resolve_config_base_dir"]
