#!/usr/bin/env python3
"""Validate that every acceptance result has an auditable code/document mapping."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = ROOT / "artifacts" / "compatibility-result.json"
DEFAULT_MAPPING = ROOT / "docs" / "acceptance-capability-map.json"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate(result_path: Path, mapping_path: Path) -> list[str]:
    result = _read(result_path)
    mapping = _read(mapping_path).get("capabilities")
    if not isinstance(mapping, dict):
        return ["mapping must contain a capabilities object"]
    errors: list[str] = []
    results = result.get("results")
    if not isinstance(results, list):
        return ["result must contain a results list"]
    for item in results:
        if not isinstance(item, dict) or not isinstance(item.get("capability_id"), str):
            errors.append("result contains an invalid capability entry")
            continue
        capability_id = item["capability_id"]
        entry = mapping.get(capability_id)
        if not isinstance(entry, dict):
            errors.append(f"{capability_id}: missing mapping")
            continue
        for field in ("tests", "source_modules", "docs", "matrix_field"):
            value = entry.get(field)
            if (isinstance(value, list) and not value) or (not isinstance(value, (list, str))):
                errors.append(f"{capability_id}: {field} is empty or invalid")
        for field in ("tests", "source_modules"):
            for raw_path in entry.get(field, []):
                if not (ROOT / str(raw_path).split("#", 1)[0]).is_file():
                    errors.append(f"{capability_id}: missing {field} path {raw_path}")
        for raw_ref in entry.get("docs", []):
            if not (ROOT / str(raw_ref).split("#", 1)[0]).is_file():
                errors.append(f"{capability_id}: missing docs path {raw_ref}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    args = parser.parse_args()
    try:
        errors = validate(args.result, args.mapping)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"acceptance mapping: FAIL\n- {exc}\n")
        return 1
    if errors:
        sys.stderr.write("acceptance mapping: FAIL\n")
        sys.stderr.write("".join(f"- {error}\n" for error in errors))
        return 1
    sys.stdout.write("acceptance mapping: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
