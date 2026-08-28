#!/usr/bin/env python3
"""Compare old/new official ``langgraph dev`` and GraphHarbor snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ValueError(f"invalid compatibility snapshot: {path}")
    return value


def _project(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in snapshot["results"]:
        if isinstance(item, dict) and isinstance(item.get("capability_id"), str):
            result[item["capability_id"]] = {
                "status": item.get("status"),
                "versions": item.get("versions"),
            }
    return result


def _diff(
    before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for capability_id in sorted(set(before) | set(after)):
        old, new = before.get(capability_id), after.get(capability_id)
        if old == new:
            continue
        changes.append(
            {
                "capability_id": capability_id,
                "before": old,
                "after": new,
                "kind": "added" if old is None else "removed" if new is None else "changed",
            }
        )
    return changes


def compare(paths: dict[str, Path]) -> dict[str, Any]:
    snapshots = {name: _project(_read(path)) for name, path in paths.items()}
    pairs = {
        "official_old_vs_new": ("official_old", "official_new"),
        "graphharbor_old_vs_new": ("graphharbor_old", "graphharbor_new"),
        "official_old_vs_graphharbor_old": ("official_old", "graphharbor_old"),
        "official_new_vs_graphharbor_new": ("official_new", "graphharbor_new"),
    }
    comparisons = {
        name: {
            "before": left,
            "after": right,
            "changes": _diff(snapshots[left], snapshots[right]),
        }
        for name, (left, right) in pairs.items()
    }
    return {
        "schema_version": 1,
        "comparison": "compatibility-four-quadrants",
        "status": "changed"
        if any(item["changes"] for item in comparisons.values())
        else "unchanged",
        "comparisons": comparisons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("official_old", "official_new", "graphharbor_old", "graphharbor_new"):
        parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("artifacts/compatibility-quadrants.json"))
    parser.add_argument(
        "--allow-missing-snapshot",
        action="store_true",
        help="non-gating mode: emit informational output when a snapshot is missing",
    )
    args = parser.parse_args()
    paths = {
        name: getattr(args, name)
        for name in ("official_old", "official_new", "graphharbor_old", "graphharbor_new")
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        result = {
            "schema_version": 1,
            "comparison": "compatibility-four-quadrants",
            "status": "informational",
            "missing_snapshots": missing,
            "comparisons": {},
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        sys.stderr.write("compatibility four-quadrant comparison: missing snapshots\n")
        return 0 if args.allow_missing_snapshot else 1
    try:
        result = compare(paths)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"compatibility four-quadrant comparison: FAIL ({exc})\n")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"compatibility four-quadrant comparison: {result['status']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
