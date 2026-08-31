"""Load the public ``langgraph.json`` graph registry without private APIs."""

from __future__ import annotations

import importlib.util
import inspect
import json
import pathlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langchain_core.runnables import RunnableConfig


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


def resolve_within_base_dir(base_dir: pathlib.Path, path_text: str) -> pathlib.Path:
    base = base_dir.resolve()
    path = (base / path_text).resolve()
    if not path.is_relative_to(base):
        raise ValueError(f"path escapes base directory: {path}")
    return path


def _load_symbol(source: str, base_dir: pathlib.Path) -> Any:
    path_text, separator, symbol = source.partition(":")
    if not separator or not symbol:
        raise ValueError(f"invalid graph source {source!r}; expected path.py:symbol")
    path = resolve_within_base_dir(base_dir, path_text)
    if not path.is_file():
        raise FileNotFoundError(f"graph source does not exist: {path}")
    module_name = f"graphharbor_graph_{path.stem}_{abs(hash(path))}"
    module_spec = importlib.util.spec_from_file_location(module_name, path)
    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"cannot load graph module {path}")
    import_roots = (base_dir, base_dir / "src")
    for import_root in reversed(import_roots):
        if import_root.is_dir() and str(import_root) not in sys.path:
            sys.path.insert(0, str(import_root))
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    value = getattr(module, symbol)
    if not callable(value) and not _is_graph(value):
        raise TypeError(f"graph {source!r} is not a compiled LangGraph or factory")
    if callable(value) and not _is_graph(value):
        _validate_factory_signature(value, source)
        signature = inspect.signature(value)
        if not any(
            parameter.kind is inspect.Parameter.POSITIONAL_ONLY
            or parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            for parameter in signature.parameters.values()
        ):
            if inspect.iscoroutinefunction(value):
                return value
            try:
                value = value()
            except Exception as exc:
                raise TypeError(f"graph factory {source!r} failed during validation") from exc
            if inspect.isawaitable(value):
                return value
            if _is_graph(value):
                return value
            raise TypeError(f"graph factory {source!r} did not return a compiled LangGraph")
    return value


def _is_graph(value: Any) -> bool:
    return callable(getattr(value, "ainvoke", None)) and callable(getattr(value, "astream", None))


def _validate_factory_signature(factory: Any, source: str) -> None:
    try:
        parameters = tuple(inspect.signature(factory).parameters.values())
    except (TypeError, ValueError) as exc:
        raise TypeError(f"cannot inspect graph factory {source!r}") from exc
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if len(positional) > 1 or any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        or (
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
        )
        for parameter in parameters
    ):
        raise TypeError(
            f"graph factory {source!r} must accept zero or one positional config argument"
        )


class GraphRegistry:
    """Immutable graph registry shared by API discovery and workers."""

    def __init__(self, graphs: dict[str, Any], *, base_dir: pathlib.Path) -> None:
        self.base_dir = base_dir.resolve()
        self._graphs: dict[str, Any] = {}
        self._checkpointer: Any | None = None
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
        self._checkpointer = checkpointer
        for graph in self._graphs.values():
            if _is_graph(graph) and hasattr(graph, "checkpointer"):
                graph.checkpointer = checkpointer

    @asynccontextmanager
    async def open(self, graph_id: str, config: RunnableConfig | None = None) -> AsyncIterator[Any]:
        """Resolve one graph for one run and close factory-owned resources."""
        value = self.get(graph_id)
        if _is_graph(value):
            yield value
            return

        factory = value
        _validate_factory_signature(factory, f"{factory!r}")
        signature = inspect.signature(factory)
        positional = tuple(
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        result = factory(config or {}) if positional else factory()
        if inspect.isawaitable(result):
            result = await result
        if hasattr(result, "__aenter__") and hasattr(result, "__aexit__"):
            async with result as graph:
                self._attach_graph_checkpointer(graph)
                _validate_graph_result(graph, graph_id)
                yield graph
            return
        self._attach_graph_checkpointer(result)
        _validate_graph_result(result, graph_id)
        yield result

    def _attach_graph_checkpointer(self, graph: Any) -> None:
        if self._checkpointer is not None and hasattr(graph, "checkpointer"):
            graph.checkpointer = self._checkpointer

    def ids(self) -> tuple[str, ...]:
        return tuple(self._graphs)

    def __len__(self) -> int:
        return len(self._graphs)


__all__ = [
    "GraphRegistry",
    "GraphSpec",
    "resolve_config_base_dir",
    "resolve_within_base_dir",
]


def _validate_graph_result(graph: Any, graph_id: str) -> None:
    if not _is_graph(graph):
        raise TypeError(f"graph factory for {graph_id!r} did not return a compiled LangGraph")
