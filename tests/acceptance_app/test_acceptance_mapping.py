from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from scripts.check_acceptance_mapping import validate

ROOT = Path(__file__).resolve().parents[2]


def test_current_acceptance_result_is_fully_mapped() -> None:
    errors = validate(
        ROOT / "artifacts" / "compatibility-result.json",
        ROOT / "docs" / "acceptance-capability-map.json",
    )
    assert not errors, errors


def test_mapping_reports_unknown_capability(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text(json.dumps({"results": [{"capability_id": "unknown", "status": "failed"}]}))
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"capabilities": {}}))
    assert validate(result, mapping) == ["unknown: missing mapping"]
