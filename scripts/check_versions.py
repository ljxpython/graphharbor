#!/usr/bin/env python3
"""After ``uv lock --check``, verify GraphHarbor and dependency versions."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_GRAPHARBOR = "0.13.0.post1"
EXPECTED_DEPENDENCIES = {
    "langgraph": "1.2.11",
    "langgraph-sdk": "0.4.3",
    "langgraph-cli": "0.4.31",
    "langgraph-checkpoint": "4.2.0",
    "langgraph-checkpoint-postgres": "3.1.2",
    "langgraph-api": "0.13.0",
}


def main() -> None:
    pkgs = {
        p["name"]: p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text())["package"]
    }
    runtime = pkgs["graphharbor-runtime"]
    graphharbor = pkgs["graphharbor"]

    if runtime != graphharbor:
        raise SystemExit(f"lockstep: graphharbor-runtime={runtime!r} graphharbor={graphharbor!r}")
    if runtime != EXPECTED_GRAPHARBOR:
        raise SystemExit(f"graphharbor packages must be {EXPECTED_GRAPHARBOR!r}, got {runtime!r}")
    mismatches = {
        name: (pkgs.get(name), expected)
        for name, expected in EXPECTED_DEPENDENCIES.items()
        if pkgs.get(name) != expected
    }
    if mismatches:
        raise SystemExit(f"dependency version mismatch: {mismatches!r}")

    root_license = (ROOT / "LICENSE").read_text()
    for rel in (
        "libs/langhost/LICENSE",
        "libs/langgraph-runtime-pg/LICENSE",
    ):
        pkg_license = (ROOT / rel).read_text()
        if pkg_license != root_license:
            raise SystemExit(f"{rel} must match root LICENSE (copy after editing)")

    print(f"ok: graphharbor={runtime}; dependencies={EXPECTED_DEPENDENCIES}")


if __name__ == "__main__":
    main()
