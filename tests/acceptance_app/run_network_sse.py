"""Run a two-client SSE disconnect/reconnect check against a remote server."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


def _validate_remote_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("cross-network SSE requires an http(s) URL with a hostname")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost":
        raise ValueError("cross-network SSE URL must not point to localhost")
    try:
        if ip_address(hostname).is_loopback:
            raise ValueError("cross-network SSE URL must not point to a loopback address")
    except ValueError as exc:
        if "must not point" in str(exc):
            raise
    return value.rstrip("/")


async def _read_frames(response: httpx.Response, count: int) -> list[tuple[str, str, str]]:
    frames: list[tuple[str, str, str]] = []
    current_id: str | None = None
    current_event = "message"
    current_data: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("id:"):
            current_id = line[3:].strip()
        elif line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            current_data.append(line[5:].strip())
        elif not line and current_id is not None:
            frames.append((current_id, current_event, "\n".join(current_data)))
            current_id, current_event, current_data = None, "message", []
            if len(frames) >= count:
                break
    return frames


def _stream_id_gt(left: str, right: str) -> bool:
    def parts(value: str) -> tuple[int, int]:
        major, _, minor = value.partition("-")
        return int(major), int(minor or 0)

    return parts(left) > parts(right)


async def run(base_url: str) -> dict[str, Any]:
    base_url = _validate_remote_url(base_url)
    async with (
        httpx.AsyncClient(base_url=base_url, timeout=30) as client,
        httpx.AsyncClient(base_url=base_url, timeout=30) as stream_client,
    ):
        assistant = (
            await client.post(
                "/assistants", json={"graph_id": "network_sse", "name": "network-gate"}
            )
        ).json()
        thread = (await client.post("/threads", json={"metadata": {"network_sse": True}})).json()
        assistant_id, thread_id = assistant["assistant_id"], thread["thread_id"]
        try:
            run_response = await client.post(
                f"/threads/{thread_id}/runs",
                json={
                    "assistant_id": assistant_id,
                    "input": {"phases": []},
                    "stream_mode": ["values"],
                },
            )
            run_response.raise_for_status()
            run_id = run_response.json()["run_id"]
            async with stream_client.stream(
                "GET",
                f"/threads/{thread_id}/runs/{run_id}/stream?stream_modes=run_modes",
                headers={"accept": "text/event-stream", "last-event-id": "-"},
            ) as first:
                first_frames = await asyncio.wait_for(_read_frames(first, 2), timeout=15)
            if not first_frames:
                raise AssertionError("first SSE client received no frames")
            cursor = first_frames[-1][0]
            async with stream_client.stream(
                "GET",
                f"/threads/{thread_id}/runs/{run_id}/stream?stream_modes=run_modes",
                headers={"accept": "text/event-stream", "last-event-id": cursor},
            ) as second:
                second.raise_for_status()
                second_frames = await asyncio.wait_for(_read_frames(second, 4), timeout=15)
            assert second_frames and all(_stream_id_gt(item[0], cursor) for item in second_frames)
            state = await client.get(f"/threads/{thread_id}/state")
            state.raise_for_status()
            assert state.json().get("values", {}).get("phases") == ["phase-complete"] * 3
            run_state = await client.get(f"/threads/{thread_id}/runs/{run_id}")
            run_state.raise_for_status()
            assert run_state.json().get("status") == "success"
            terminal_event = any(
                event == "end" or '"phase-complete","phase-complete","phase-complete"' in data
                for _, event, data in second_frames
            )
            assert terminal_event, second_frames
            return {
                "status": "passed",
                "run_id": run_id,
                "first_client_frames": len(first_frames),
                "reconnected_frames": len(second_frames),
                "cursor": cursor,
                "duplicate_frames": 0,
                "terminal_event": "final_values",
            }
        finally:
            await client.delete(f"/threads/{thread_id}")
            await client.delete(f"/assistants/{assistant_id}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="remote GraphHarbor URL")
    parser.add_argument(
        "--result-out", type=Path, default=Path("artifacts/network-sse-result.json")
    )
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args.base_url))
    except (ValueError, httpx.HTTPError, TimeoutError) as exc:
        sys.stderr.write(f"network SSE acceptance failed: {exc}\n")
        return 1
    args.result_out.parent.mkdir(parents=True, exist_ok=True)
    args.result_out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
