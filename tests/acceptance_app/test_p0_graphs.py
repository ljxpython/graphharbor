"""Fast checks for the complex acceptance fixtures (no server or provider)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from p0_graphs import assistant_supervisor, network_sse
from run_network_sse import _validate_remote_url


def test_supervisor_map_reduce_fixture() -> None:
    result = assistant_supervisor.invoke({"request": "acceptance"})
    assert len(result["findings"]) == 2
    assert "billing" in result["summary"] and "technical" in result["summary"]


def test_network_fixture_has_three_phases() -> None:
    result = asyncio.run(network_sse.ainvoke({"phases": []}))
    assert result["phases"] == ["phase-complete"] * 3


def test_cross_network_sse_rejects_loopback_urls() -> None:
    for value in ("http://localhost:31296", "http://127.0.0.1:31296", "http://[::1]:31296"):
        try:
            _validate_remote_url(value)
        except ValueError:
            continue
        raise AssertionError(f"loopback URL was accepted: {value}")


def test_cross_network_sse_accepts_remote_url() -> None:
    assert (
        _validate_remote_url("https://acceptance.example.test") == "https://acceptance.example.test"
    )
