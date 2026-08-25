#!/usr/bin/env python3
"""Validate the frozen GraphHarbor compatibility baseline.

This gate freezes the public LangGraph SDK set and keeps ``langgraph-api`` visible
only as an internal comparison spike, not as a production requirement.
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "langgraph": "1.2.11",
    "langgraph-sdk": "0.4.3",
    "langgraph-cli": "0.4.31",
    "langgraph-checkpoint": "4.2.0",
    "langgraph-checkpoint-postgres": "3.1.2",
}


def _locked_packages() -> dict[str, str]:
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {item["name"]: item["version"] for item in lock.get("package", [])}


def _check_versions() -> list[str]:
    packages = _locked_packages()
    errors = [
        f"{name}={packages.get(name)!r}; expected {expected!r}"
        for name, expected in TARGETS.items()
        if packages.get(name) != expected
    ]
    if packages.get("langgraph-api") != "0.13.0":
        errors.append("langgraph-api must remain at the internal 0.13.0 comparison version")
    return errors


def _check_runtime_profile() -> list[str]:
    errors: list[str] = []
    if os.environ.get("LANGGRAPH_CLOUD_LICENSE_KEY"):
        errors.append("LANGGRAPH_CLOUD_LICENSE_KEY must be unset for the production profile")
    if os.environ.get("LANGSMITH_API_KEY") and os.environ.get("GRAPHHARBOR_REQUIRE_NO_LANGSMITH") == "1":
        errors.append("LANGSMITH_API_KEY must be unset when GRAPHARBOR_REQUIRE_NO_LANGSMITH=1")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="print current and target versions")
    args = parser.parse_args()

    packages = _locked_packages()
    if args.report:
        for name in [*TARGETS, "langgraph-api", "langgraph-runtime-inmem"]:
            sys.stdout.write(
                f"{name}: current={packages.get(name, '<missing>')} "
                f"target={TARGETS.get(name, 'spike-only')}\n"
            )

    errors = [*_check_versions(), *_check_runtime_profile()]
    if errors:
        sys.stderr.write("compatibility baseline: FAIL\n")
        for error in errors:
            sys.stderr.write(f"- {error}\n")
        return 1
    sys.stdout.write("compatibility baseline: OK\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
