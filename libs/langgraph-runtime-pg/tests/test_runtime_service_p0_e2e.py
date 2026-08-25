"""Live GraphHarbor E2E for the five runtime-service production graphs.

This suite is intentionally opt-in: it must run against a real GraphHarbor
HTTP server with the runtime-service provider/tool dependencies configured.
"""

from __future__ import annotations

import os
import time
from typing import Any

import pytest

if not os.environ.get("GRAPHHARBOR_P0_E2E"):
    pytest.skip(
        "set GRAPHHARBOR_P0_E2E=1 to run live provider-backed P0 graph E2E",
        allow_module_level=True,
    )


pytestmark = pytest.mark.e2e

BASE_URL = os.environ.get("GRAPHHARBOR_URL", "http://127.0.0.1:31296")
TOKEN = os.environ.get("GRAPHHARBOR_P0_E2E_TOKEN", "").strip()
API_KEY = os.environ.get("GRAPHHARBOR_P0_E2E_API_KEY", "").strip()
SUITE = f"graphharbor-p0-{int(time.time())}"

P0_INPUTS: dict[str, dict[str, Any]] = {
    "assistant": {
        "messages": [{"role": "user", "content": "Reply with exactly READY."}],
    },
    "test_case_agent_v2": {
        "messages": [
            {
                "role": "user",
                "content": "Summarize this requirement in one sentence: users can export a report.",
            }
        ],
    },
    "customer_support_handoffs_demo": {
        "messages": [{"role": "user", "content": "I need help with a product warranty."}],
    },
    "deepagent_demo": {
        "messages": [{"role": "user", "content": "Reply with exactly READY."}],
    },
    "personal_assistant_demo": {
        "messages": [{"role": "user", "content": "Reply with exactly READY."}],
    },
}


def _headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


@pytest.mark.asyncio
@pytest.mark.parametrize("graph_id", sorted(P0_INPUTS))
async def test_p0_graph_runs_over_official_sdk(graph_id: str) -> None:
    from langgraph_sdk import get_client

    client = get_client(url=BASE_URL, headers=_headers(), timeout=900)
    assistant = None
    thread = None
    try:
        assistant = await client.assistants.create(
            graph_id=graph_id,
            name=f"{SUITE}-{graph_id}",
            metadata={"suite": SUITE},
        )
        thread = await client.threads.create(
            graph_id=graph_id,
            metadata={"suite": SUITE, "graph_id": graph_id},
        )
        parts = [
            part
            async for part in client.runs.stream(
                thread["thread_id"],
                assistant["assistant_id"],
                input=P0_INPUTS[graph_id],
                stream_mode=["values", "updates"],
                stream_subgraphs=True,
                stream_resumable=True,
                context={
                    "user_id": "graphharbor-p0-e2e",
                    "tenant_id": "graphharbor-e2e",
                    "project_id": "graphharbor-p0",
                    "role": "operator",
                    "permissions": ["runs:write", "threads:read"],
                },
                version="v2",
            )
        ]
        assert parts, f"{graph_id} produced no v2 stream events"
        runs = await client.runs.list(thread["thread_id"], limit=1)
        assert runs, f"{graph_id} did not persist a run"
        assert runs[0]["status"] in {"success", "interrupted"}, runs[0]
    finally:
        if thread is not None:
            await client.threads.delete(thread["thread_id"])
        if assistant is not None:
            await client.assistants.delete(assistant["assistant_id"])
        await client.aclose()
