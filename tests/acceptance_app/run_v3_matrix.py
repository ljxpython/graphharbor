"""Real HTTP checks for nested scopes, partial resume and durable run streams."""

import argparse
import asyncio
import json
from pathlib import Path

import httpx
from run_v3_probe import capture


async def stable(client, thread, run):
    for _ in range(200):
        response = await client.get(f"/threads/{thread}/runs/{run}")
        response.raise_for_status()
        row = response.json()
        if row["status"] not in {"pending", "running"}:
            return row
        await asyncio.sleep(0.1)
    raise AssertionError("run did not settle")


async def multi(client):
    thread = (await client.post("/threads", json={})).json()["thread_id"]
    row = (
        await client.post(
            f"/threads/{thread}/runs",
            json={
                "assistant_id": "v3_multi",
                "input": {"scenario": "interrupted"},
                "version": "v3",
            },
        )
    ).json()
    await stable(client, thread, row["run_id"])
    state = (await client.get(f"/threads/{thread}/state")).json()
    interrupts = [i for t in state["tasks"] for i in t["interrupts"]]
    assert len(interrupts) == 2, state
    runs = []
    for index, item in enumerate(interrupts):
        before = {r["run_id"] for r in (await client.get(f"/threads/{thread}/runs")).json()}
        command = {
            "id": index + 1,
            "method": "input.respond",
            "params": {
                "interrupt_id": item["id"],
                "response": item["value"]["label"].upper(),
            },
        }
        response = (await client.post(f"/threads/{thread}/commands", json=command)).json()
        assert response.get("type") == "success", response
        new = [
            r
            for r in (await client.get(f"/threads/{thread}/runs")).json()
            if r["run_id"] not in before
        ]
        assert len(new) == 1, new
        await stable(client, thread, new[0]["run_id"])
        runs.append(new[0]["run_id"])
        state = (await client.get(f"/threads/{thread}/state")).json()
        pending = [i for t in state["tasks"] if t.get("result") is None for i in t["interrupts"]]
        assert len(pending) == 1 - index, state
    assert sorted(state["values"]["results"]) == ["alpha:ALPHA", "beta:BETA"], state
    return {"thread_id": thread, "runs": runs, "results": state["values"]["results"]}


async def run(url):
    async with httpx.AsyncClient(base_url=url, timeout=30) as client:
        nested = await capture(client, "v3_nested", "completed")
        assert max(len(e["params"]["namespace"]) for e in nested["events"]) == 2
        caught = await capture(client, "v3_caught", "completed")
        # The frozen engine may not emit child lifecycle for ainvoke inside a node.
        # Preserve what it emits and require the caught error not to fail the root.
        state = (await client.get(f"/threads/{caught['thread_id']}/state")).json()
        assert state["values"]["results"] == ["caught"], state
        resumed = await multi(client)
        return {"nested": nested, "caught": caught, "multi": resumed}


async def transport(url):
    async with httpx.AsyncClient(base_url=url, timeout=20, trust_env=False) as client:
        case = await capture(client, "v3_nested", "completed")
        events = case["events"]
        for scope, depth in [([], 0), ([], 1), (events[1]["params"]["namespace"], 0)]:
            expected = [
                e
                for e in events
                if e["params"]["namespace"][: len(scope)] == scope
                and len(e["params"]["namespace"]) - len(scope) <= depth
            ]
            assert expected
            for _ in range(2):
                got = []
                async with client.stream(
                    "POST",
                    f"/threads/{case['thread_id']}/stream/events",
                    json={
                        "channels": ["lifecycle"],
                        "namespaces": [scope],
                        "depth": depth,
                        "since": 0,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith("data:"):
                            got.append(json.loads(line[5:]))
                            if len(got) == len(expected):
                                break
                assert got == expected
        streams = []
        for subgraphs in [False, True]:
            thread = (await client.post("/threads", json={})).json()["thread_id"]
            response = await client.post(
                f"/threads/{thread}/runs/stream",
                json={
                    "assistant_id": "v3_nested",
                    "input": {},
                    "version": "v3",
                    "stream_subgraphs": subgraphs,
                    "stream_resumable": True,
                },
            )
            response.raise_for_status()
            location = response.headers["content-location"]
            replay = await client.get(location + "/stream")

            def typed(text):
                return [
                    x
                    for line in text.splitlines()
                    if line.startswith("data:")
                    and isinstance(x := json.loads(line[5:]), dict)
                    and "method" in x
                ]

            live_events = typed(response.text)
            assert live_events == typed(replay.text)
            assert len({e["seq"] for e in live_events}) == len(live_events)
            nested = [
                e
                for e in live_events
                if e["params"].get("namespace")
                or (e["method"] == "lifecycle" and e["params"]["data"].get("namespace"))
            ]
            assert bool(nested) == subgraphs
            cursor = live_events[len(live_events) // 2]["seq"]
            resumed = await client.get(location + "/stream", headers={"Last-Event-ID": str(cursor)})
            assert typed(resumed.text) == [e for e in live_events if e["seq"] > cursor]
            assert response.text.startswith("event: metadata\n")
            streams.append({"subgraphs": subgraphs, "events": len(live_events), "cursor": cursor})
        thread = (await client.post("/threads", json={})).json()["thread_id"]
        async with client.stream(
            "POST",
            f"/threads/{thread}/runs/stream",
            json={
                "assistant_id": "v3_slow",
                "input": {"label": "20"},
                "version": "v3",
            },
        ) as response:
            heartbeat = False
            async for line in response.aiter_lines():
                heartbeat |= line.startswith(": heartbeat")
            assert heartbeat
        return {
            "nested_filters": "passed",
            "repeated_subscriptions": "passed",
            "streams": streams,
            "heartbeat": heartbeat,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--transport", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(asyncio.wait_for(run(args.url), 120))
    if args.transport:
        report["transport"] = asyncio.run(asyncio.wait_for(transport(args.url), 120))
    args.output.write_text(json.dumps(report, indent=2) + "\n")
