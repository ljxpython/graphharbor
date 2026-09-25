"""Compare bounded replay behavior without recording event payloads."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx


async def frames(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    limit: int = 20,
    **kwargs: object,
) -> dict[str, object]:
    result: dict[str, object] = {"frames": []}
    async with client.stream(method, path, **kwargs) as response:
        result["status"] = response.status_code
        result["content_location"] = response.headers.get("content-location")
        if response.status_code >= 400:
            result["error"] = (await response.aread()).decode()[:200]
            return result
        frame: dict[str, str] = {}
        try:
            async with asyncio.timeout(3):
                async for line in response.aiter_lines():
                    if line.startswith(("id:", "event:", "data:")):
                        key, _, value = line.partition(":")
                        if key == "data":
                            try:
                                data = json.loads(value)
                                if isinstance(data, dict):
                                    frame["method"] = str(data.get("method", ""))
                                    frame["seq"] = str(data.get("seq", ""))
                            except ValueError:
                                pass
                        else:
                            frame[key] = value.strip()
                    elif not line and frame:
                        result["frames"].append(frame)
                        frame = {}
                        if len(result["frames"]) >= limit:
                            break
        except TimeoutError:
            result["timed_out"] = True
    return result


async def probe(url: str, resumable: bool = True) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=url, timeout=8) as client:
        response = await client.post("/threads", json={})
        response.raise_for_status()
        thread = response.json()["thread_id"]
        initial = await frames(
            client,
            "POST",
            f"/threads/{thread}/runs/stream",
            json={
                "assistant_id": "v3_edge",
                "input": {"label": "replay-probe"},
                "stream_mode": ["values", "updates"],
                "stream_resumable": resumable,
            },
        )
        location = str(initial.get("content_location") or "")
        run = location.rsplit("/", 1)[-1]
        if not run or run == location:
            return {"initial": initial, "error": "run id unavailable"}
        ids = [f.get("id") for f in initial["frames"] if f.get("id")]
        last_id = str(ids[0]) if ids else "0"
        joined = await frames(
            client,
            "GET",
            f"/threads/{thread}/runs/{run}/stream",
            headers={"Last-Event-ID": last_id},
        )
        thread_all = await frames(
            client,
            "GET",
            f"/threads/{thread}/stream",
            headers={"Last-Event-ID": "-"},
        )
        thread_resume_id = next((f["id"] for f in thread_all["frames"] if f.get("id")), "0-0")
        thread_resume = await frames(
            client,
            "GET",
            f"/threads/{thread}/stream",
            headers={"Last-Event-ID": thread_resume_id},
        )
        protocol = await frames(
            client,
            "POST",
            f"/threads/{thread}/stream/events",
            json={"channels": ["lifecycle"], "since": 0},
        )
        return {
            "thread": thread,
            "run": run,
            "initial": initial,
            "join": joined,
            "thread_all": thread_all,
            "thread_resume": thread_resume,
            "protocol": protocol,
        }


async def probe_existing(
    url: str, thread: str, run: str, last_id: str, since: int = 0
) -> dict[str, object]:
    async with httpx.AsyncClient(base_url=url, timeout=8) as client:
        return {
            "thread_status": (await client.get(f"/threads/{thread}")).status_code,
            "run_status": (await client.get(f"/threads/{thread}/runs/{run}")).status_code,
            "join": await frames(
                client,
                "GET",
                f"/threads/{thread}/runs/{run}/stream",
                headers={"Last-Event-ID": last_id},
            ),
            "thread_all": await frames(
                client,
                "GET",
                f"/threads/{thread}/stream",
                headers={"Last-Event-ID": "-"},
            ),
            "protocol": await frames(
                client,
                "POST",
                f"/threads/{thread}/stream/events",
                json={"channels": ["lifecycle"], "since": since},
            ),
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--thread")
    parser.add_argument("--run")
    parser.add_argument("--last-id", default="0")
    parser.add_argument("--since", type=int, default=0)
    parser.add_argument("--no-resumable", action="store_true")
    args = parser.parse_args()
    result = (
        probe_existing(args.url, args.thread, args.run, args.last_id, args.since)
        if args.thread and args.run
        else probe(args.url, not args.no_resumable)
    )
    sys.stdout.write(json.dumps(asyncio.run(result), indent=2) + "\n")
