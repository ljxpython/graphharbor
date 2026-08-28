from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.compare_compatibility_results import _versions_changed, main


def _result(path: Path) -> None:
    path.write_text(
        json.dumps({"results": [{"capability_id": "basic", "status": "passed"}]}),
        encoding="utf-8",
    )


def test_missing_baseline_is_a_gate_unless_explicitly_allowed(
    tmp_path: Path, monkeypatch: object
) -> None:
    current = tmp_path / "current.json"
    _result(current)
    missing = tmp_path / "missing.json"
    out = tmp_path / "diff.json"
    followups = tmp_path / "followups.md"
    argv = [
        "compare_compatibility_results.py",
        "--current",
        str(current),
        "--baseline",
        str(missing),
        "--out",
        str(out),
        "--followups",
        str(followups),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 1
    assert json.loads(out.read_text(encoding="utf-8"))["missing_baseline"] is True

    monkeypatch.setattr(sys, "argv", [*argv, "--allow-missing-baseline"])
    assert main() == 0


def test_minimal_baseline_version_scope_ignores_new_metadata() -> None:
    assert not _versions_changed(
        {"langgraph": "1.2.11"},
        {"langgraph": "1.2.11", "langchain": "1.3.17"},
    )
    assert _versions_changed(
        {"langgraph": "1.2.11"},
        {"langgraph": "1.2.12", "langchain": "1.3.17"},
    )
