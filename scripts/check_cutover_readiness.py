#!/usr/bin/env python3
"""Evaluate the production cutover gates without allowing an implicit green state."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GATES = ROOT / "artifacts" / "cutover-gates.json"
DEFAULT_OUTPUT = ROOT / "artifacts" / "cutover-readiness.json"
STATUSES = {"passed", "failed", "blocked_external_dependency", "not_run"}
REQUIRED_GATES = (
    "runtime_context_policy",
    "worker_recovery",
    "deepagent_isolation",
    "mcp_scope_and_lifecycle",
    "observability_redaction_and_fail_soft",
    "cross_network_sse",
    "external_runtime_dependencies",
    "migration_and_backup_restore",
    "performance_budget",
    "platform_route_ownership",
    "rollout_and_rollback",
)


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _valid_evidence(evidence: Any) -> bool:
    if not isinstance(evidence, list) or not evidence:
        return False
    root = ROOT.resolve()
    for raw_path in evidence:
        if not isinstance(raw_path, str) or not raw_path.strip():
            return False
        path = (ROOT / raw_path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return False
    return True


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    raw_gates = payload.get("gates")
    gates = raw_gates if isinstance(raw_gates, dict) else {}
    evaluated: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for gate in REQUIRED_GATES:
        raw = gates.get(gate)
        item = raw if isinstance(raw, dict) else {}
        status = item.get("status", "not_run")
        if status not in STATUSES:
            status = "failed"
            blockers.append(f"{gate}:invalid_status")
        evidence = item.get("evidence")
        invalid_evidence = not _valid_evidence(evidence)
        if status == "passed" and invalid_evidence:
            status = "failed"
            blockers.append(f"{gate}:evidence_file_missing")
        if status != "passed":
            blockers.append(f"{gate}:{status}")
        evaluated[gate] = {
            "status": status,
            "evidence": evidence if isinstance(evidence, list) else [],
            "failure": item.get("failure"),
        }

    owner_approved = payload.get("owner_approved") is True
    if not owner_approved:
        blockers.append("owner_approved:false")
    return {
        "schema_version": 1,
        "status": "ready_for_cutover" if not blockers else "not_ready",
        "owner_approved": owner_approved,
        "gates": evaluated,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gates", type=Path, default=DEFAULT_GATES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        result = evaluate(_read(args.gates))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"cutover readiness: FAIL\n- {exc}\n")
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sys.stdout.write(f"cutover readiness: {result['status']}\n")
    for blocker in result["blockers"]:
        sys.stdout.write(f"- {blocker}\n")
    return 0 if result["status"] == "ready_for_cutover" else 1


if __name__ == "__main__":
    raise SystemExit(main())
