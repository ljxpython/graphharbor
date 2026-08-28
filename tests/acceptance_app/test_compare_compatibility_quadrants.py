from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.compare_compatibility_quadrants import compare, main


def _snapshot(path: Path, status: str = "passed") -> None:
    path.write_text(
        json.dumps(
            {
                "results": [
                    {"capability_id": "basic", "status": status, "versions": {"langgraph": "1"}}
                ]
            }
        ),
        encoding="utf-8",
    )


def test_four_quadrants_report_official_and_runtime_changes(tmp_path: Path) -> None:
    paths = {
        name: tmp_path / f"{name}.json"
        for name in ("official_old", "official_new", "graphharbor_old", "graphharbor_new")
    }
    for path in paths.values():
        _snapshot(path)
    _snapshot(paths["official_new"], "failed")
    result = compare(paths)
    assert result["status"] == "changed"
    assert result["comparisons"]["official_old_vs_new"]["changes"]
    assert result["comparisons"]["graphharbor_old_vs_new"]["changes"] == []


def test_missing_snapshot_is_a_gate_unless_allowed(tmp_path: Path, monkeypatch: object) -> None:
    labels = ("official-old", "official-new", "graphharbor-old", "graphharbor-new")
    paths = [tmp_path / f"{label}.json" for label in labels]
    out = tmp_path / "quadrants.json"
    argv = ["compare_compatibility_quadrants.py"]
    for label, path in zip(labels, paths, strict=True):
        argv.extend((f"--{label}", str(path)))
    argv.extend(("--out", str(out)))
    monkeypatch.setattr(sys, "argv", argv)
    assert main() == 1
    monkeypatch.setattr(sys, "argv", [*argv, "--allow-missing-snapshot"])
    assert main() == 0
