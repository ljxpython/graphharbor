"""Bounded v2/v3 HTTP comparison; no model, no performance framework."""

import argparse
import asyncio
import json
import statistics
import subprocess
import time
from pathlib import Path

import httpx


def rss(pids):
    return sum(
        int(
            subprocess.check_output(
                ["rtk", "proxy", "ps", "-o", "rss=", "-p", str(pid)],
                text=True,
            ).strip()
        )
        for pid in pids
    )


async def sample(client, version):
    thread = (await client.post("/threads", json={})).json()["thread_id"]
    start = time.perf_counter()
    first = None
    count = size = 0
    async with client.stream(
        "POST",
        f"/threads/{thread}/runs/stream",
        json={
            "assistant_id": "v3_edge",
            "input": {},
            "version": version,
            "stream_mode": ["values"],
            "stream_subgraphs": True,
            "stream_resumable": True,
        },
    ) as response:
        response.raise_for_status()
        location = response.headers["content-location"]

        async def observe_terminal():
            while True:
                row = (await client.get(location)).json()
                if row["status"] not in {"pending", "running"}:
                    assert row["status"] == "success", row
                    return time.perf_counter()
                await asyncio.sleep(0.01)

        observer = asyncio.create_task(observe_terminal())
        async for line in response.aiter_lines():
            size += len(line.encode()) + 1
            if line.startswith("event:"):
                first = first or time.perf_counter()
                count += 1
    ended = time.perf_counter()
    terminal_seen = await asyncio.wait_for(observer, 5)
    run = (await client.get(location)).json()
    assert run["status"] == "success", run
    # Root lifecycle is emitted after commit. EOF is the corresponding v2 signal.
    assert first is not None
    return {
        "version": version,
        "run_id": run["run_id"],
        "thread_id": thread,
        "first_event_ms": (first - start) * 1000,
        "stream_end_ms": (ended - start) * 1000,
        "terminal_read_after_eof_ms": (time.perf_counter() - ended) * 1000,
        "stream_end_minus_terminal_observed_ms": (ended - terminal_seen) * 1000,
        "events": count,
        "bytes": size,
    }


async def main(args):
    async with httpx.AsyncClient(base_url=args.url, timeout=30, trust_env=False) as client:
        for version in ("v2", "v3"):
            await sample(client, version)
        rows = []
        for _ in range(20):
            for version in ("v2", "v3"):
                row = await sample(client, version)
                row["server_worker_rss_kib"] = rss(args.pid)
                rows.append(row)
        summary = {}
        for version in ("v2", "v3"):
            group = [r for r in rows if r["version"] == version]
            summary[version] = {"runs": len(group), "errors": 0}
            for key in (
                "first_event_ms",
                "stream_end_ms",
                "terminal_read_after_eof_ms",
                "stream_end_minus_terminal_observed_ms",
                "events",
                "bytes",
            ):
                summary[version][key + "_median"] = statistics.median(r[key] for r in group)
            summary[version]["rss_kib_sampled_max"] = max(r["server_worker_rss_kib"] for r in group)
        # A later v2 run must not change how an already finished v3 run replays.
        previous = next(r for r in rows if r["version"] == "v3")
        replay = await client.get(
            f"/threads/{previous['thread_id']}/runs/{previous['run_id']}/stream"
        )
        assert '"method":"lifecycle"' in replay.text
        args.output.write_text(
            json.dumps(
                {
                    "summary": summary,
                    "samples": rows,
                    "rollback": "new v2 succeeded; historical v3 lifecycle retained",
                    "memory": "API+Worker RSS sampled after each run; not allocation peak",
                    "terminal_latency": "concurrent 10ms HTTP polling observes committed terminal; signed EOF delta includes polling/network error, not exact DB commit instrumentation",
                },
                indent=2,
            )
            + "\n"
        )
        print(json.dumps(summary, indent=2))  # noqa: T201


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--pid", type=int, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
