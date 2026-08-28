from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.compare_p0_graphs import (
    _differences,
    _matches_tool_trace,
    _normalize_tool_trace,
    _projection,
)


def test_p0_projection_detects_structured_regression() -> None:
    expected = {"findings": 2, "summary": True}
    official = {"status": "success", "findings": 2, "summary_present": True}
    graphharbor = {"status": "success", "findings": 1, "summary_present": True}
    assert _differences(
        _projection("p0_assistant", official, expected),
        _projection("p0_assistant", graphharbor, expected),
    )


def test_p0_projection_ignores_nondeterministic_agent_text() -> None:
    expected = {"markers": ("PASS",), "tool_trace": ("read_scope", "run_validation")}
    official = {"status": "success", "tool_trace": ["read_scope", "run_validation"]}
    graphharbor = {"status": "success", "tool_trace": ["read_scope", "run_validation"]}
    assert not _differences(
        _projection("test_case_agent_v2", official, expected),
        _projection("test_case_agent_v2", graphharbor, expected),
    )


def test_p0_projection_detects_tool_order_or_count_regression() -> None:
    expected = {"markers": ("PASS",), "tool_trace": ("read_scope", "run_validation")}
    official = {"status": "success", "tool_trace": ["read_scope", "run_validation"]}
    graphharbor = {"status": "success", "tool_trace": ["run_validation", "read_scope"]}
    assert _differences(
        _projection("test_case_agent_v2", official, expected),
        _projection("test_case_agent_v2", graphharbor, expected),
    )


def test_deep_agent_trace_compacts_only_consecutive_progress_updates() -> None:
    assert _normalize_tool_trace(
        ["write_todos", "write_todos", "task", "write_todos", "write_todos"], compact=True
    ) == ["write_todos", "task", "write_todos"]
    assert _normalize_tool_trace(["write_todos", "task", "write_todos"], compact=True) == [
        "write_todos",
        "task",
        "write_todos",
    ]


def test_deep_agent_trace_requires_planning_research_and_completion_order() -> None:
    expected = ("write_todos", "task", "write_todos")
    assert _matches_tool_trace(
        ["write_todos", "write_todos", "task", "write_todos"], expected, mode="subsequence"
    )
    assert not _matches_tool_trace(["task", "write_todos"], expected, mode="subsequence")
