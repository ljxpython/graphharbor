"""Keep upstream RemoteGraph fixtures on this test environment's server."""

import os


def pytest_collection_modifyitems(items):
    url = os.environ["LANGGRAPH_INTEGRATION_URL"]
    for module in {item.module for item in items}:
        # Upstream's v3 module hardcodes localhost:2024 instead of its URL fixture.
        if module.__name__.split(".")[-1] == "test_remote_graph_v3":
            module.URL = url
