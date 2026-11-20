"""The upstream test adapter must not point at another environment."""

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_remote_graph_url_uses_owned_server(monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts" / "upstream_sdk_fixtures.py"
    spec = importlib.util.spec_from_file_location("upstream_sdk_fixtures", path)
    assert spec is not None and spec.loader is not None
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    remote = ModuleType("test_remote_graph_v3")
    remote.URL = "http://localhost:2024"
    other = ModuleType("test_other")
    other.URL = "http://localhost:2024"
    monkeypatch.setenv("LANGGRAPH_INTEGRATION_URL", "http://127.0.0.1:49123")

    plugin.pytest_collection_modifyitems(
        [
            SimpleNamespace(module=remote),
            SimpleNamespace(module=remote),
            SimpleNamespace(module=other),
        ]
    )

    assert remote.URL == "http://127.0.0.1:49123"
    assert other.URL == "http://localhost:2024"
