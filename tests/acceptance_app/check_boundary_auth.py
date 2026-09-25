"""Compare identity-only authorization on isolated official and GraphHarbor servers."""

from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4

from scripts.compare_official_protocol import _request


def check(base_url: str) -> dict[str, object]:
    alice_id, bob_id = str(uuid4()), str(uuid4())

    def call(method: str, path: str, user: str | None = None, body: object = None):
        return _request(
            base_url,
            method,
            path,
            15,
            body,
            {"Authorization": user} if user else None,
        )

    assert call("POST", "/threads", body={}).status == 401
    for user, thread_id in (("alice", alice_id), ("bob", bob_id)):
        created = call(
            "POST", "/threads", user, {"thread_id": thread_id, "metadata": {"owner": "forged"}}
        )
        assert created.status == 200, created
        assert created.body["metadata"]["owner"] == user, created

    results: dict[str, object] = {}
    cases = (
        ("own_read", "GET", f"/threads/{alice_id}", "alice", None),
        ("other_read", "GET", f"/threads/{alice_id}", "bob", None),
        ("other_update", "PATCH", f"/threads/{alice_id}", "bob", {"metadata": {"owner": "bob"}}),
        ("other_delete", "DELETE", f"/threads/{alice_id}", "bob", None),
        ("other_state", "GET", f"/threads/{alice_id}/state", "bob", None),
        ("other_history", "POST", f"/threads/{alice_id}/history", "bob", {}),
        ("other_run", "POST", f"/threads/{alice_id}/runs", "bob", {"assistant_id": "basic", "input": {}}),
        ("own_search", "POST", "/threads/search", "alice", {"metadata": {"owner": "alice"}}),
        ("other_search", "POST", "/threads/search", "bob", {"metadata": {"owner": "alice"}}),
    )
    for name, method, path, user, body in cases:
        response = call(method, path, user, body)
        results[name] = response.status
        if name == "own_search":
            assert response.status == 200 and any(
                item["thread_id"] == alice_id for item in response.body
            ), response
        elif name == "other_search":
            assert response.status == 200 and not any(
                item["thread_id"] == alice_id for item in response.body
            ), response
        elif name.startswith("other_"):
            assert response.status in {403, 404}, response

    basic = call(
        "POST", f"/threads/{alice_id}/runs/wait", "alice",
        {"assistant_id": "basic", "input": {"value": 6}},
    )
    assert basic.status == 200 and basic.body == {"value": 7, "trace": ["basic"]}, basic
    results["basic_wait"] = basic.status

    hitl = call(
        "POST", f"/threads/{alice_id}/runs/wait", "alice",
        {"assistant_id": "hitl", "input": {"question": "approve?"}},
    )
    assert hitl.status == 200 and hitl.body["question"] == "approve?", hitl
    assert len(hitl.body["__interrupt__"]) == 1, hitl
    assert hitl.body["__interrupt__"][0]["value"] == {"question": "approve?"}, hitl
    results["hitl_wait"] = hitl.status

    denied_stream = call("GET", f"/threads/{alice_id}/stream", "bob")
    assert denied_stream.status == 200, denied_stream
    assert denied_stream.headers["content-type"] == "text/event-stream", denied_stream
    assert "event: error" in denied_stream.body, denied_stream
    assert '"message":"404: Thread not found"' in denied_stream.body, denied_stream
    results["other_stream"] = denied_stream.status

    unchanged = call("GET", f"/threads/{alice_id}", "alice")
    assert unchanged.status == 200 and unchanged.body["metadata"]["owner"] == "alice"
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--official-url", required=True)
    parser.add_argument("--graphharbor-url", required=True)
    args = parser.parse_args()
    official = check(args.official_url)
    graphharbor = check(args.graphharbor_url)
    assert official == graphharbor, {"official": official, "graphharbor": graphharbor}
    sys.stdout.write(json.dumps({"status": "passed", "cases": graphharbor}, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
