"""Compare two capability-level acceptance snapshots."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ValueError(f"invalid compatibility result: {path}")
    return value


def _versions_changed(before: Any, after: Any) -> bool:
    """Compare the version scope recorded by the baseline snapshot.

    Baselines are intentionally minimal capability snapshots, so a newer result
    may contain additional package metadata without creating a false diff.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return before != after
    return any(after.get(name) != version for name, version in before.items())


def compare(baseline_path: Path | None, current_path: Path) -> tuple[dict[str, Any], str]:
    current = _read(current_path)
    if baseline_path is None or not baseline_path.is_file():
        return (
            {
                "schema_version": 1,
                "comparison": "capability-level",
                "status": "informational",
                "baseline": str(baseline_path) if baseline_path else None,
                "current_result": str(current_path),
                "capability_changes": [],
                "missing_baseline": True,
            },
            "# Compatibility follow-ups\n\n首次运行没有 previous snapshot; 下一次升级前请保存当前结果作为 baseline。\n",
        )
    baseline = _read(baseline_path)
    before = {item["capability_id"]: item for item in baseline["results"] if isinstance(item, dict)}
    after = {item["capability_id"]: item for item in current["results"] if isinstance(item, dict)}
    changes: list[dict[str, Any]] = []
    followups = ["# Compatibility follow-ups", ""]
    for capability_id in sorted(set(before) | set(after)):
        old, new = before.get(capability_id), after.get(capability_id)
        if old is None or new is None:
            kind = "capability_added" if old is None else "capability_removed"
        elif old.get("status") != new.get("status"):
            kind = (
                "regression"
                if new.get("status") not in {"passed", "informational"}
                else "behavior_change"
            )
        elif _versions_changed(old.get("versions"), new.get("versions")):
            kind = "dependency_version_change"
        else:
            continue
        changes.append(
            {
                "capability_id": capability_id,
                "kind": kind,
                "before_status": old.get("status") if old else None,
                "after_status": new.get("status") if new else None,
                "before_versions": old.get("versions") if old else None,
                "after_versions": new.get("versions") if new else None,
            }
        )
        followups.append(f"- `{capability_id}`: `{kind}`; 检查对应测试、源码适配和兼容文档。")
    if not changes:
        followups.append("- 未发现能力级状态或依赖版本变化。")
    return (
        {
            "schema_version": 1,
            "comparison": "capability-level",
            "status": "changed" if changes else "unchanged",
            "baseline": str(baseline_path),
            "current_result": str(current_path),
            "capability_changes": changes,
            "missing_baseline": False,
        },
        "\n".join(followups) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, default=Path("artifacts/compatibility-result.json"))
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--out", type=Path, default=Path("artifacts/compatibility-diff.json"))
    parser.add_argument(
        "--followups", type=Path, default=Path("artifacts/compatibility-followups.md")
    )
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        help="non-gating mode: allow a first comparison without a baseline",
    )
    args = parser.parse_args()
    diff, followups = compare(args.baseline, args.current)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(diff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    args.followups.write_text(followups, encoding="utf-8")
    return 0 if not diff.get("missing_baseline") or args.allow_missing_baseline else 1


if __name__ == "__main__":
    raise SystemExit(main())
