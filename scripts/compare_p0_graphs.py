#!/usr/bin/env python3
"""Run the five acceptance P0 graphs against pinned ``langgraph dev`` and GraphHarbor."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx

CASES: tuple[tuple[str, dict[str, Any], dict[str, Any]], ...] = (
    ("p0_assistant", {"request": "triage"}, {"findings": 2, "summary": True}),
    (
        "customer_support_handoffs_demo",
        {"issue": "refund duplicate charge"},
        {"response": "refund approved"},
    ),
    (
        "test_case_agent_v2",
        {
            "messages": [
                {
                    "role": "user",
                    "content": "Create checkout test cases for checkout with attachment checkout.png.",
                }
            ]
        },
        {
            "markers": ("PASS",),
            "tool_trace": ("read_project_scope", "fetch_requirements", "run_validation"),
        },
    ),
    (
        "personal_assistant_demo",
        {"messages": [{"role": "user", "content": "Plan a meeting tomorrow for alice."}]},
        {
            "markers": ("proposal",),
            "tool_trace": ("read_preference", "coordinate_delegate", "draft_schedule"),
            "booking": "booking confirmed",
        },
    ),
    (
        "deepagent_demo",
        {"messages": [{"role": "user", "content": "Research GraphHarbor architecture."}]},
        {
            "markers": ("PostgreSQL",),
            "tool_trace": ("write_todos", "task", "write_todos"),
            "compact_tool_trace": True,
            "trace_mode": "subsequence",
        },
    ),
)


def _text(value: Any) -> str:
    if isinstance(value, dict):
        content = value.get("content", "")
        if isinstance(content, list):
            return " ".join(_text(item) for item in content)
        return str(content)
    return str(value)


def _normalize_tool_trace(trace: list[str], *, compact: bool) -> list[str]:
    if not compact:
        return trace
    result: list[str] = []
    for tool in trace:
        if not result or result[-1] != tool:
            result.append(tool)
    return result


def _matches_tool_trace(trace: list[str], expected: tuple[str, ...], *, mode: str) -> bool:
    if mode == "exact":
        return trace == list(expected)
    if mode == "subsequence":
        expected_iter = iter(expected)
        current = next(expected_iter, None)
        for tool in trace:
            if tool == current:
                current = next(expected_iter, None)
        return current is None
    raise ValueError(f"unsupported P0 tool trace mode: {mode}")


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
    messages = values.get("messages") if isinstance(values, dict) else []
    messages = messages if isinstance(messages, list) else []
    tool_trace: list[str] = []
    rendered: list[str] = []
    message_kinds: list[str] = []
    for message in messages:
        if isinstance(message, dict):
            message_kinds.append(str(message.get("type") or message.get("role") or "unknown"))
            rendered.append(_text(message))
            calls = message.get("tool_calls") or []
            tool_trace.extend(
                str(call.get("name"))
                for call in calls
                if isinstance(call, dict) and call.get("name")
            )
    return {
        "status": payload.get("status"),
        "response": values.get("response") if isinstance(values, dict) else None,
        "booking": values.get("booking") if isinstance(values, dict) else None,
        "route": values.get("route") if isinstance(values, dict) else None,
        "specialist": values.get("specialist") if isinstance(values, dict) else None,
        "approved": values.get("approved") if isinstance(values, dict) else None,
        "findings": len(values.get("findings", [])) if isinstance(values, dict) else 0,
        "summary_present": bool(values.get("summary")) if isinstance(values, dict) else False,
        "interrupts": bool(payload.get("interrupts")),
        "messages": len(messages),
        "tool_trace": tool_trace,
        "message_kinds": message_kinds,
        "text": " ".join(rendered),
    }


def _projection(graph_id: str, summary: dict[str, Any], expected: dict[str, Any]) -> dict[str, Any]:
    if graph_id == "p0_assistant":
        return {
            "status": summary["status"],
            "findings": summary["findings"],
            "summary_present": summary["summary_present"],
        }
    if graph_id == "customer_support_handoffs_demo":
        return {
            key: summary[key] for key in ("status", "route", "specialist", "approved", "response")
        }
    if graph_id == "personal_assistant_demo":
        return {
            "status": summary["status"],
            "booking": summary["booking"],
            "required_tool_trace": list(expected["tool_trace"]),
            "observed_tool_trace": _normalize_tool_trace(summary["tool_trace"], compact=False),
        }
    if expected.get("trace_mode") == "subsequence":
        return {
            "status": summary["status"],
            "required_tool_trace": list(expected["tool_trace"]),
            "tool_trace_contract": "matched",
        }
    return {
        "status": summary["status"],
        "required_tool_trace": list(expected["tool_trace"]),
        "observed_tool_trace": _normalize_tool_trace(
            summary["tool_trace"], compact=bool(expected.get("compact_tool_trace"))
        ),
    }


def _differences(official: Any, graphharbor: Any, path: str = "$") -> list[dict[str, Any]]:
    if type(official) is not type(graphharbor):
        return [{"path": path, "official": official, "graphharbor": graphharbor}]
    if isinstance(official, dict):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(official) | set(graphharbor)):
            if key not in official or key not in graphharbor:
                differences.append(
                    {
                        "path": f"{path}.{key}",
                        "official": official.get(key),
                        "graphharbor": graphharbor.get(key),
                    }
                )
            else:
                differences.extend(_differences(official[key], graphharbor[key], f"{path}.{key}"))
        return differences
    if isinstance(official, list):
        differences = []
        for index, (left, right) in enumerate(zip(official, graphharbor, strict=False)):
            differences.extend(_differences(left, right, f"{path}[{index}]"))
        if len(official) != len(graphharbor):
            differences.append(
                {
                    "path": f"{path}.length",
                    "official": len(official),
                    "graphharbor": len(graphharbor),
                }
            )
        return differences
    return (
        []
        if official == graphharbor
        else [{"path": path, "official": official, "graphharbor": graphharbor}]
    )


def _safe_failure(exc: BaseException) -> str:
    if isinstance(exc, AssertionError):
        return "AssertionError: P0 acceptance invariant failed"
    return f"{type(exc).__name__}: {str(exc)[:500]}"


async def _request(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> dict[str, Any]:
    response = await client.request(method, path, **kwargs)
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError(f"{method} {path} returned a non-object")
    return value


def _interrupt_id(*payloads: dict[str, Any]) -> str | None:
    for payload in payloads:
        raw = payload.get("interrupts") or payload.get("__interrupt__") or []
        values = list(raw.values()) if isinstance(raw, dict) else raw
        if not isinstance(values, list) or not values:
            continue
        first = values[0]
        if isinstance(first, dict):
            value = first.get("interrupt_id") or first.get("id")
            if value:
                return str(value)
    return None


async def _terminal_payload(
    client: httpx.AsyncClient,
    thread_id: str,
    assistant_id: str,
    initial: dict[str, Any],
    graph_id: str,
) -> tuple[dict[str, Any], str]:
    state = await _request(client, "GET", f"/threads/{thread_id}/state")
    interrupt_id = _interrupt_id(initial, state)
    if (
        graph_id not in {"customer_support_handoffs_demo", "personal_assistant_demo"}
        or not interrupt_id
    ):
        runs = await client.get(f"/threads/{thread_id}/runs", params={"limit": 1})
        runs.raise_for_status()
        run_values = runs.json()
        status = (
            run_values[0].get("status") if isinstance(run_values, list) and run_values else None
        )
        return initial, str(status or "success")

    command = await _request(
        client,
        "POST",
        f"/threads/{thread_id}/commands",
        json={
            "id": 1,
            "method": "input.respond",
            "params": {"interrupt_id": interrupt_id, "response": True},
        },
    )
    resumed = command
    if not ((command.get("result") or {}).get("run_id")):
        resumed = await _request(
            client,
            "POST",
            f"/threads/{thread_id}/runs/wait",
            json={"assistant_id": assistant_id, "command": {"resume": True}},
        )
    values = resumed.get("values") if isinstance(resumed.get("values"), dict) else resumed
    expected_key = "response" if graph_id == "customer_support_handoffs_demo" else "booking"
    if values.get(expected_key):
        return resumed, "success"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        state = await _request(client, "GET", f"/threads/{thread_id}/state")
        values = state.get("values") or {}
        if values.get(expected_key):
            return state, "success"
        await asyncio.sleep(0.1)
    raise TimeoutError(f"{graph_id} did not reach a terminal state after resume")


async def _run_one(
    base_url: str, graph_id: str, input_value: dict[str, Any], expected: dict[str, Any]
) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=120) as client:
        assistant = await _request(
            client, "POST", "/assistants", json={"graph_id": graph_id, "name": f"p0-{graph_id}"}
        )
        thread = await _request(client, "POST", "/threads", json={"graph_id": graph_id})
        thread_id = str(thread["thread_id"])
        try:
            result = await _request(
                client,
                "POST",
                f"/threads/{thread_id}/runs/wait",
                json={"assistant_id": assistant["assistant_id"], "input": input_value},
            )
            result, terminal_status = await _terminal_payload(
                client, thread_id, str(assistant["assistant_id"]), result, graph_id
            )
            summary = _summary(result)
            summary["status"] = terminal_status
            if graph_id == "p0_assistant":
                assert summary["findings"] == expected["findings"] and summary["summary_present"]
            elif graph_id == "customer_support_handoffs_demo":
                assert (
                    summary["status"] == "success" and summary["response"] == expected["response"]
                )
            elif graph_id == "personal_assistant_demo":
                assert summary["status"] == "success" and summary["booking"] == expected["booking"]
            else:
                text = summary["text"].lower()
                assert summary["messages"] >= 3
                assert all(str(marker).lower() in text for marker in expected["markers"])
                observed_trace = _normalize_tool_trace(
                    summary["tool_trace"], compact=bool(expected.get("compact_tool_trace"))
                )
                assert set(observed_trace) <= set(expected["tool_trace"]), observed_trace
                assert _matches_tool_trace(
                    observed_trace,
                    expected["tool_trace"],
                    mode=str(expected.get("trace_mode", "exact")),
                ), observed_trace
            summary.pop("text", None)
            return {"summary": summary, "projection": _projection(graph_id, summary, expected)}
        finally:
            await client.delete(f"/threads/{thread_id}")
            await client.delete(f"/assistants/{assistant['assistant_id']}")


async def _main(args: argparse.Namespace) -> dict[str, Any]:
    services = {"official": args.official_url, "graphharbor": args.graphharbor_url}
    results: dict[str, Any] = {name: {} for name in services}
    for service, url in services.items():
        for graph_id, input_value, expected in CASES:
            try:
                execution = await _run_one(url, graph_id, input_value, expected)
                results[service][graph_id] = {
                    "status": "passed",
                    **execution,
                }
            except Exception as exc:
                results[service][graph_id] = {
                    "status": "failed",
                    "failure": _safe_failure(exc),
                }
    pair: dict[str, Any] = {}
    for graph_id, _input, _expected in CASES:
        left = results["official"][graph_id]
        right = results["graphharbor"][graph_id]
        if left["status"] == right["status"] == "passed":
            differences = _differences(left["projection"], right["projection"])
        else:
            differences = [
                {
                    "path": "$.execution_status",
                    "official": left["status"],
                    "graphharbor": right["status"],
                    "official_failure": left.get("failure"),
                    "graphharbor_failure": right.get("failure"),
                }
            ]
        pair[graph_id] = {
            "status": "passed"
            if left["status"] == right["status"] == "passed" and not differences
            else "failed",
            "official": left,
            "graphharbor": right,
            "differences": differences,
        }
    overall = "passed" if all(item["status"] == "passed" for item in pair.values()) else "failed"
    return {
        "schema_version": 1,
        "status": overall,
        "official_url": args.official_url,
        "graphharbor_url": args.graphharbor_url,
        "graphs": pair,
        "difference_count": sum(len(item["differences"]) for item in pair.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-url", required=True)
    parser.add_argument("--graphharbor-url", required=True)
    parser.add_argument("--result-out", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_main(args))
    args.result_out.parent.mkdir(parents=True, exist_ok=True)
    args.result_out.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
