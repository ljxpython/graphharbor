"""Capture bounded, deterministic official/GraphHarbor protocol evidence."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx


async def capture(client: httpx.AsyncClient, graph: str, scenario: str) -> dict:
    response = await client.post("/threads", json={})
    response.raise_for_status()
    thread = response.json()["thread_id"]
    response = await client.post(
        f"/threads/{thread}/commands",
        json={
            "id": 1,
            "method": "run.start",
            "params": {
                "assistant_id": graph,
                "input": {"scenario": scenario} if graph != "v3_tool" else {"messages": []},
            },
        },
    )
    response.raise_for_status()
    command = response.json()
    if "error" in command:
        return {"graph": graph, "scenario": scenario, "command": command}
    events = []
    async with client.stream(
        "POST", f"/threads/{thread}/stream/events", json={"channels": ["lifecycle"], "since": 0}
    ) as stream:
        stream.raise_for_status()
        async for line in stream.aiter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event.get("method") != "lifecycle":
                continue
            events.append(event)
            params = event["params"]
            data = params["data"]
            if (
                not params.get("namespace")
                and not data.get("namespace")
                and data.get("event")
                in {
                    "completed",
                    "failed",
                    "interrupted",
                }
            ):
                break
    run = command["result"]["run_id"]
    assert events[-1]["params"]["data"]["event"] == scenario
    result = {
        "graph": graph,
        "scenario": scenario,
        "thread_id": thread,
        "run_id": run,
        "events": events,
    }
    if scenario == "interrupted":
        source_before = (await client.get(f"/threads/{thread}/runs/{run}")).json()["status"]
        while source_before in {"pending", "running"}:
            await asyncio.sleep(0.1)
            source_before = (await client.get(f"/threads/{thread}/runs/{run}")).json()["status"]
        state = (await client.get(f"/threads/{thread}/state")).json()
        interrupt_id = next(item["id"] for task in state["tasks"] for item in task["interrupts"])
        response = await client.post(
            f"/threads/{thread}/commands",
            json={
                "id": 2,
                "method": "input.respond",
                "params": {"interrupt_id": interrupt_id, "response": True},
            },
        )
        response.raise_for_status()
        command_result = response.json()
        assert command_result["type"] == "success", command_result
        # Official input.respond acknowledges acceptance without returning a run ID.
        # This probe owns an isolated thread with exactly one original run.
        resumed = command_result.get("result", {}).get("run_id")
        while resumed is None:
            response = await client.get(f"/threads/{thread}/runs")
            response.raise_for_status()
            candidates = [item["run_id"] for item in response.json() if item["run_id"] != run]
            assert len(candidates) <= 1, candidates
            resumed = candidates[0] if candidates else None
            if resumed is None:
                await asyncio.sleep(0.1)
        while True:
            status = (await client.get(f"/threads/{thread}/runs/{resumed}")).json()["status"]
            if status not in {"pending", "running"}:
                break
            await asyncio.sleep(0.1)
        assert status == "success", status
        source_after = (await client.get(f"/threads/{thread}/runs/{run}")).json()["status"]
        assert source_after == source_before, (source_before, source_after)
        result["resume"] = {
            "run_id": resumed,
            "status": status,
            "source_status": source_after,
            "source_run_preserved": True,
        }
    return result


async def verify_replay(client: httpx.AsyncClient, case: dict) -> list[dict]:
    """Verify durable replay, cursor continuation and one exact child scope."""
    original = case["events"]
    checks = []
    for since, scope in [
        (0, None),
        (original[1]["seq"], None),
        (0, original[1]["params"]["namespace"]),
    ]:
        expected = [
            event
            for event in original
            if event["seq"] > since and (scope is None or event["params"]["namespace"] == scope)
        ]
        body = {"channels": ["lifecycle"], "since": since}
        if scope is not None:
            body.update(namespaces=[scope], depth=0)
        received = []
        async with client.stream(
            "POST", f"/threads/{case['thread_id']}/stream/events", json=body
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    received.append(json.loads(line[5:]))
                    if len(received) == len(expected):
                        break
        assert received == expected, (received, expected)
        checks.append(
            {"since": since, "namespace": scope, "events": len(received), "exact_match": True}
        )
    return checks


async def probe(url: str, replay: bool = False) -> dict:
    async with httpx.AsyncClient(base_url=url, timeout=20) as client:
        report = {"info": (await client.get("/info")).json(), "scenarios": []}
        for graph, scenario in [
            ("v3_edge", "completed"),
            ("v3_edge", "failed"),
            ("v3_edge", "interrupted"),
            ("v3_send", "completed"),
            ("v3_tool", "completed"),
        ]:
            report["scenarios"].append(await asyncio.wait_for(capture(client, graph, scenario), 30))
        if replay:
            case = next(case for case in report["scenarios"] if case["graph"] == "v3_send")
            report["replay"] = await asyncio.wait_for(verify_replay(client, case), 30)
        thread = (await client.post("/threads", json={})).json()["thread_id"]
        result = await client.post(
            f"/threads/{thread}/runs/stream",
            json={
                "assistant_id": "v3_edge",
                "input": {"label": "probe"},
                "version": "v3",
                "stream_mode": ["values"],
                "stream_subgraphs": True,
            },
        )
        report["run_sse_v3"] = {"status": result.status_code, "body": result.text}
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-replay", action="store_true")
    args = parser.parse_args()
    report = asyncio.run(probe(args.url, args.verify_replay))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for case in report["scenarios"]:
        phases = [e["params"]["data"]["event"] for e in case.get("events", [])]
        sys.stdout.write(f"{case['graph']} {case['scenario']} {phases}\n")
