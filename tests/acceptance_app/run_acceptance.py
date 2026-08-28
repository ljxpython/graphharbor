"""Run the fixed GraphHarbor acceptance graphs and emit one safe result file."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from langgraph_sdk import get_client

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "artifacts" / "compatibility-result.json"


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _versions() -> dict[str, str]:
    names = (
        "graphharbor",
        "graphharbor-runtime",
        "langgraph",
        "langgraph-sdk",
        "langgraph-cli",
        "langchain",
        "langchain-openai",
        "deepagents",
        "mcp",
        "langchain-mcp-adapters",
    )
    return {name: importlib.metadata.version(name) for name in names if _installed(name)}


def _installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _external_dependency_failure(exc: BaseException) -> bool:
    if isinstance(exc, (AssertionError, KeyError, TypeError, ValueError)):
        return False
    message = str(exc).upper()
    return isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError, OSError)) or any(
        marker in message for marker in ("DEEPSEEK", "OPENAI_API_KEY", "API KEY", "PROVIDER")
    )


def _safe_failure(exc: BaseException) -> str:
    if isinstance(exc, AssertionError):
        return "AssertionError: acceptance invariant failed"
    message = str(exc)
    for name in ("DEEPSEEK_PROXY_API_KEY", "DEEPSEEK_PROXY_URL"):
        value = os.environ.get(name)
        if value:
            message = message.replace(value, "[redacted]")
    return f"{type(exc).__name__}: {message[:500]}"


def _record(
    capability_id: str, tier: str, tests: list[str], status: str, **kwargs: Any
) -> dict[str, Any]:
    return {
        "capability_id": capability_id,
        "tier": tier,
        "tests": tests,
        "status": status,
        "versions": _versions(),
        "evidence": kwargs.pop("evidence", {}),
        "failure": kwargs.pop("failure", None),
        **kwargs,
    }


async def _health(base_url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        values = {}
        for path in ("/ok", "/ready", "/info", "/metrics"):
            response = await client.get(path)
            response.raise_for_status()
            values[path] = response.status_code
        return values


async def _resource_case(
    client: Any, graph_id: str, input_value: dict[str, Any], *, version: str = "v2"
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    assistant = await client.assistants.create(graph_id=graph_id, name=f"acceptance-{graph_id}")
    thread = await client.threads.create(graph_id=graph_id, metadata={"acceptance": True})
    try:
        parts = [
            part
            async for part in client.runs.stream(
                thread["thread_id"],
                assistant["assistant_id"],
                input=input_value,
                stream_mode=["values", "updates"],
                stream_subgraphs=True,
                stream_resumable=True,
                version=version,
            )
        ]
        runs = await client.runs.list(thread["thread_id"], limit=1)
        if not runs:
            raise AssertionError(f"{graph_id} did not persist a run")
        if runs[0]["status"] not in {"success", "interrupted"}:
            raise AssertionError(f"{graph_id} ended in {runs[0]['status']}")
        state = await client.threads.get_state(thread["thread_id"])
        history = await client.threads.get_history(thread["thread_id"], limit=20)
        return runs[0], state.get("values") or {}, parts, history
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _basic_case(client: Any) -> dict[str, Any]:
    run, _state, parts, history = await _resource_case(client, "basic", {"value": 1})
    data = [part.get("data") for part in parts if isinstance(part, dict)]
    assert any(isinstance(item, dict) and item.get("value") == 2 for item in data)
    assert history and any(
        isinstance(item, dict) and (item.get("values") or {}).get("value") == 2 for item in history
    ), history
    return _record(
        "basic_run_persistence",
        "deterministic_protocol",
        ["python_sdk_runs_stream", "postgres_run_persistence"],
        "passed",
        evidence={
            "status": run["status"],
            "stream_events": len(parts),
            "value_2_seen": True,
            "history_entries": len(history),
        },
    )


async def _subgraph_case(client: Any) -> dict[str, Any]:
    assistant = await client.assistants.create(graph_id="subgraph", name="acceptance-subgraph-v3")
    thread = await client.threads.create(graph_id="subgraph")
    events: list[dict[str, Any]] = []
    try:
        async with client.threads.stream(
            thread_id=thread["thread_id"], assistant_id=assistant["assistant_id"]
        ) as stream:

            async def collect() -> None:
                async for event in stream.events:
                    if isinstance(event, dict):
                        events.append(event)
                        params = event.get("params") or {}
                        if event.get("method") == "lifecycle" and params.get("status") in {
                            "completed",
                            "errored",
                        }:
                            return

            collector = asyncio.create_task(collect())
            await stream.run.start(input={"steps": []})
            final = await stream.output
            try:
                await asyncio.wait_for(collector, timeout=5)
            except TimeoutError:
                collector.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await collector
        assert final.get("steps") == ["child"], final
        runs = await client.runs.list(thread["thread_id"], limit=1)
        assert runs and runs[0]["status"] == "success", runs
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])
    typed = [event for event in events if event.get("method")]
    assert typed, "v3 subgraph stream did not expose typed events"
    assert any(event.get("method") == "lifecycle" for event in typed), typed
    namespaces = [
        (event.get("params") or {}).get("namespace")
        for event in typed
        if (event.get("params") or {}).get("namespace")
    ]
    assert namespaces, "v3 subgraph stream did not expose a namespace"
    return _record(
        "subgraph_namespace_v3",
        "deterministic_protocol",
        ["python_sdk_subgraph_stream_v3", "typed_lifecycle_projection"],
        "passed",
        evidence={
            "status": runs[0]["status"],
            "namespaced_events": len(namespaces),
            "typed_events": len(typed),
            "lifecycle_event": True,
        },
    )


async def _tool_case(client: Any) -> dict[str, Any]:
    run, state, _parts, _history = await _resource_case(client, "tool", {"a": 3, "b": 4})
    assert state.get("result") == 12, state
    assert (state.get("tool_calls") or [{}])[0].get("name") == "multiply", state
    return _record(
        "tool_call_state",
        "deterministic_protocol",
        ["python_sdk_tool_graph"],
        "passed",
        evidence={"status": run["status"], "tool": "multiply", "result": 12},
    )


async def _inprocess_streaming_case(client: Any) -> dict[str, Any]:
    assistant = await client.assistants.create(
        graph_id="streaming_all_modes", name="acceptance-streaming-all-modes"
    )
    thread = await client.threads.create(graph_id="streaming_all_modes")
    modes = ["values", "updates", "messages", "custom", "checkpoints", "tasks", "debug"]
    try:
        parts = [
            part
            async for part in client.runs.stream(
                thread["thread_id"],
                assistant["assistant_id"],
                input={"value": 1},
                stream_mode=modes,
                stream_subgraphs=True,
                stream_resumable=True,
                version="v2",
            )
        ]
        emitted = {
            str(part.get("event") or part.get("type"))
            for part in parts
            if isinstance(part, dict) and (part.get("event") or part.get("type"))
        }
        assert set(modes) <= emitted, {
            "expected": modes,
            "emitted": sorted(emitted),
            "parts": parts,
        }
        return _record(
            "inprocess_streaming_all_modes",
            "deterministic_protocol",
            ["python_sdk_v2_stream_parts", "durable_remote_replay"],
            "passed",
            evidence={"modes": modes, "emitted": sorted(emitted), "events": len(parts)},
        )
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _replay_case(client: Any, base_url: str) -> dict[str, Any]:
    assistant = await client.assistants.create(graph_id="basic", name="acceptance-replay")
    thread = await client.threads.create(graph_id="basic")
    try:
        parts = [
            part
            async for part in client.runs.stream(
                thread["thread_id"],
                assistant["assistant_id"],
                input={"value": 1},
                stream_mode="values",
                stream_resumable=True,
                version="v2",
            )
        ]
        assert len(parts) >= 3, parts
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as raw:
            replay = await raw.get(
                f"/threads/{thread['thread_id']}/runs/{parts[0]['data']['run_id']}/stream",
                headers={"last-event-id": "1"},
            )
        replay.raise_for_status()
        assert "id: 1" not in replay.text and "id: 2" in replay.text, replay.text
        return _record(
            "sse_replay_cursor",
            "deterministic_protocol",
            ["python_sdk_resumable_stream", "rest_last_event_id"],
            "passed",
            evidence={"initial_events": len(parts), "cursor": "1", "replayed_after_cursor": True},
        )
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _javascript_case(base_url: str) -> dict[str, Any]:
    env = {**os.environ, "GRAPHHARBOR_URL": base_url}
    proc = await asyncio.create_subprocess_exec(
        "node",
        "contract.mjs",
        cwd=ROOT / "tests" / "javascript",
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        line = (await asyncio.wait_for(proc.stdout.readline(), timeout=45)).decode()
        if '"ok":true' not in line.replace(" ", ""):
            stderr = (await proc.stderr.read()).decode()
            raise RuntimeError(f"javascript contract failed: {line.strip()} {stderr[-1000:]}")
    except BaseException:
        proc.kill()
        await proc.wait()
        raise
    else:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            proc.kill()
            await proc.wait()
    return _record(
        "javascript_sdk_contract",
        "deterministic_protocol",
        ["tests/javascript/contract.mjs"],
        "passed",
        evidence={"stdout": line[-500:], "terminated_after_success": True},
    )


async def _hitl_case(client: Any, base_url: str) -> dict[str, Any]:
    assistant = await client.assistants.create(graph_id="hitl", name="acceptance-hitl")
    thread = await client.threads.create(graph_id="hitl")
    try:
        async with client.threads.stream(
            thread_id=thread["thread_id"], assistant_id=assistant["assistant_id"]
        ) as stream:
            await stream.run.start(input={"question": "Approve acceptance run?"})
            deadline = time.monotonic() + 30
            async for _ in stream.values:
                if stream.interrupted or time.monotonic() >= deadline:
                    break
            assert stream.interrupted and stream.interrupts
            interrupt_id = stream.interrupts[0]["interrupt_id"]
            await stream.run.respond("yes", interrupt_id=interrupt_id)
            final = await stream.output
            assert final.get("approved") == "yes", final
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as raw:
            duplicate = await raw.post(
                f"/threads/{thread['thread_id']}/commands",
                json={
                    "id": 2,
                    "method": "input.respond",
                    "params": {
                        "interrupt_id": interrupt_id,
                        "response": "yes",
                    },
                },
            )
        duplicate.raise_for_status()
        duplicate_result = duplicate.json().get("result") or {}
        runs = await client.runs.list(thread["thread_id"], limit=3)
        assert runs and runs[0]["status"] == "success", runs
        assert duplicate_result.get("run_id") == runs[0]["run_id"], duplicate_result
        return _record(
            "hitl_interrupt_resume",
            "deterministic_protocol",
            ["python_sdk_interrupt", "command_resume"],
            "passed",
            evidence={
                "interrupt_id_present": True,
                "resumed": True,
                "duplicate_resume_idempotent": True,
                "terminal_status": runs[0]["status"],
            },
        )
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _chat_case(client: Any) -> dict[str, Any]:
    run, state, parts, _history = await _resource_case(
        client,
        "chat",
        {"messages": [{"role": "user", "content": "Reply with exactly READY."}]},
    )
    assert state.get("provider") == "deepseek", state
    assert state.get("provider_streamed") is True and int(state.get("token_count", 0)) > 0, state
    return _record(
        "real_deepseek_agent",
        "real_provider",
        ["python_sdk_runs_stream", "deepseek_proxy_sse"],
        "passed",
        evidence={
            "status": run["status"],
            "stream_events": len(parts),
            "provider": "deepseek",
            "model": state.get("model", ""),
            "provider_token_chunks": int(state["token_count"]),
            "runtime_sse_token_projection": False,
        },
    )


def _message_text(message: Any) -> str:
    content = (
        message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    )
    if isinstance(content, list):
        return " ".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content
        )
    return str(content)


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


def _tool_call_trace(messages: list[Any]) -> list[str]:
    return [
        str(call["name"])
        for message in messages
        if isinstance(message, dict)
        for call in (message.get("tool_calls") or [])
        if isinstance(call, dict) and call.get("name")
    ]


async def _agent_case(client: Any, graph_id: str, capability_id: str) -> dict[str, Any]:
    run, state, parts, _history = await _resource_case(
        client,
        graph_id,
        {
            "messages": [
                {"role": "user", "content": "Use lookup_fact once, then answer in one sentence."}
            ]
        },
    )
    messages = state.get("messages") or []
    text = " ".join(_message_text(message) for message in messages)
    assert len(messages) >= 3 and "GraphHarbor" in text and "PostgreSQL" in text, messages
    return _record(
        capability_id,
        "real_provider",
        [
            "python_sdk_agent_stream",
            "deepagents_tool_loop" if graph_id == "deep_agent" else "langchain_tool_loop",
        ],
        "passed",
        evidence={
            "status": run["status"],
            "messages": len(messages),
            "stream_events": len(parts),
            "tool_name": "lookup_fact",
        },
    )


async def _store_case(client: Any) -> dict[str, Any]:
    namespace = ["acceptance", "store", f"run-{int(time.time() * 1000)}"]
    key = "item"
    value = {"value": 1}
    await client.store.put_item(namespace, key, value)
    try:
        item = await client.store.get_item(namespace, key)
        assert item and item.get("value") == value, item
        search = await client.store.search_items(namespace[:2])
        assert any(entry.get("key") == key for entry in search.get("items", [])), search
        namespaces = await client.store.list_namespaces(prefix=namespace[:2])
        assert any(entry == namespace for entry in namespaces.get("namespaces", [])), namespaces
    finally:
        await client.store.delete_item(namespace, key)
    assert await client.store.get_item(namespace, key) is None
    return _record(
        "store_lifecycle",
        "deterministic_protocol",
        ["python_sdk_store_put_get_search_namespaces_delete"],
        "passed",
        evidence={"get": True, "search": True, "namespaces": True, "delete": True},
    )


async def _p0_supervisor_case(client: Any) -> dict[str, Any]:
    run, state, parts, _history = await _resource_case(
        client, "assistant_supervisor", {"request": "triage acceptance request"}
    )
    findings = state.get("findings") or []
    assert len(findings) == 2 and state.get("summary"), state
    return _record(
        "p0_assistant_supervisor",
        "deterministic_protocol",
        ["langgraph_send_map_reduce", "subgraph_lifecycle"],
        "passed",
        evidence={"status": run["status"], "findings": len(findings), "stream_events": len(parts)},
    )


async def _p0_handoff_case(client: Any, base_url: str) -> dict[str, Any]:
    assistant = await client.assistants.create(
        graph_id="customer_support_handoff", name="p0-handoff"
    )
    thread = await client.threads.create(graph_id="customer_support_handoff")
    try:
        async with client.threads.stream(
            thread_id=thread["thread_id"], assistant_id=assistant["assistant_id"]
        ) as stream:
            await stream.run.start(input={"issue": "refund duplicate charge"})
            async for _ in stream.values:
                if stream.interrupted:
                    break
            assert stream.interrupted and stream.interrupts
            interrupt_id = stream.interrupts[0]["interrupt_id"]
            await stream.run.respond(True, interrupt_id=interrupt_id)
            final = await stream.output
            assert final.get("response") == "refund approved", final
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as raw:
            duplicate = await raw.post(
                f"/threads/{thread['thread_id']}/commands",
                json={
                    "id": 2,
                    "method": "input.respond",
                    "params": {"interrupt_id": interrupt_id, "response": True},
                },
            )
        duplicate.raise_for_status()
        runs = await client.runs.list(thread["thread_id"], limit=3)
        assert runs and duplicate.json().get("result", {}).get("run_id") == runs[0]["run_id"]
        return _record(
            "p0_customer_support_handoff",
            "deterministic_protocol",
            ["langgraph_command_handoff", "hitl_resume_idempotency"],
            "passed",
            evidence={"route": "billing", "interrupt": True, "duplicate_resume": True},
        )
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _p0_agent_case(
    client: Any,
    graph_id: str,
    capability_id: str,
    marker: str,
    prompt: str,
    expected_tool_trace: tuple[str, ...],
    compact_tool_trace: bool = False,
    trace_mode: str = "exact",
) -> dict[str, Any]:
    run, state, parts, _history = await _resource_case(
        client,
        graph_id,
        {"messages": [{"role": "user", "content": prompt}]},
    )
    messages = state.get("messages") or []
    if not messages:
        for part in reversed(parts):
            data = part.get("data") if isinstance(part, dict) else None
            if isinstance(data, dict) and data.get("messages"):
                messages = data["messages"]
                break
    rendered = " ".join(_message_text(message) for message in messages)
    assert len(messages) >= 3 and marker.lower() in rendered.lower(), messages
    raw_tool_trace = _tool_call_trace(messages)
    tool_trace = _normalize_tool_trace(raw_tool_trace, compact=compact_tool_trace)
    assert set(tool_trace) <= set(expected_tool_trace), tool_trace
    assert _matches_tool_trace(tool_trace, expected_tool_trace, mode=trace_mode), tool_trace
    return _record(
        capability_id,
        "real_provider",
        ["python_sdk_agent_stream", "tool_loop"],
        "passed",
        evidence={
            "status": run["status"],
            "messages": len(messages),
            "stream_events": len(parts),
            "tool_trace": tool_trace,
            "raw_tool_trace": raw_tool_trace,
        },
    )


async def _p0_personal_assistant_case(client: Any, base_url: str) -> dict[str, Any]:
    assistant = await client.assistants.create(
        graph_id="personal_assistant_demo", name="p0-personal"
    )
    thread = await client.threads.create(graph_id="personal_assistant_demo")
    try:
        async with client.threads.stream(
            thread_id=thread["thread_id"], assistant_id=assistant["assistant_id"]
        ) as stream:
            await stream.run.start(
                input={
                    "messages": [
                        {
                            "role": "user",
                            "content": "Plan a meeting tomorrow for user alice; return a proposal.",
                        }
                    ]
                }
            )
            async for _ in stream.values:
                if stream.interrupted:
                    break
            assert stream.interrupted and stream.interrupts
            interrupt_id = stream.interrupts[0]["interrupt_id"]
            await stream.run.respond(True, interrupt_id=interrupt_id)
            final = await stream.output
            assert final.get("booking") == "booking confirmed", final
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as raw:
            duplicate = await raw.post(
                f"/threads/{thread['thread_id']}/commands",
                json={
                    "id": 2,
                    "method": "input.respond",
                    "params": {"interrupt_id": interrupt_id, "response": True},
                },
            )
        duplicate.raise_for_status()
        runs = await client.runs.list(thread["thread_id"], limit=3)
        assert runs and duplicate.json().get("result", {}).get("run_id") == runs[0]["run_id"]
        tool_trace = _tool_call_trace(final.get("messages") or [])
        assert tool_trace == ["read_preference", "coordinate_delegate", "draft_schedule"], (
            tool_trace
        )
        return _record(
            "p0_personal_assistant",
            "real_provider",
            ["langchain_agent_supervisor", "hitl_resume_idempotency"],
            "passed",
            evidence={
                "interrupt": True,
                "booking": "booking confirmed",
                "duplicate_resume": True,
                "tool_trace": tool_trace,
            },
        )
    finally:
        await client.threads.delete(thread["thread_id"])
        await client.assistants.delete(assistant["assistant_id"])


async def _mcp_case(client: Any) -> dict[str, Any]:
    run, state, parts, _history = await _resource_case(
        client, "mcp_agent", {"topic": "GraphHarbor"}
    )
    assert state.get("mcp_tool") == "project_fact", state
    assert "GraphHarbor" in str(state.get("mcp_result")), state
    return _record(
        "mcp_external_tool_call",
        "external_integration",
        ["mcp_streamable_http", "mcp_tool_discovery"],
        "passed",
        evidence={"status": run["status"], "tool": state["mcp_tool"], "stream_events": len(parts)},
    )


async def _mcp_transport_case(base_url: str) -> dict[str, Any]:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    mcp_client = MultiServerMCPClient(
        {"graphharbor": {"transport": "http", "url": f"{base_url.rstrip('/')}/mcp/"}}
    )
    tools = await mcp_client.get_tools()
    names = {tool.name for tool in tools}
    assert "basic" in names, names
    tool = next(tool for tool in tools if tool.name == "basic")
    result = await tool.ainvoke({"input": {"value": 1}})
    content = result if isinstance(result, list) else [result]
    rendered = " ".join(
        str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content
    )
    assert '"value": 2' in rendered, result
    return _record(
        "mcp_graphharbor_transport",
        "mcp_transport",
        ["mcp_streamable_http", "graphharbor_mcp_discovery", "graphharbor_mcp_call"],
        "passed",
        evidence={"tool_count": len(tools), "tool": "basic", "result_type": type(result).__name__},
    )


async def _network_sse_case(client: Any) -> dict[str, Any]:
    run, state, parts, history = await _resource_case(
        client, "network_sse", {"phases": []}, version="v2"
    )
    assert state.get("phases") == ["phase-complete"] * 3, state
    assert history
    return _record(
        "cross_network_sse_fixture",
        "network_transport",
        ["multi_phase_stream", "cursor_reconnect_external_harness"],
        "passed",
        evidence={"status": run["status"], "events": len(parts), "phases": 3},
    )


async def _cross_network_case(remote_url: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(ROOT / "tests" / "acceptance_app" / "run_network_sse.py"),
        "--base-url",
        remote_url,
        "--result-out",
        str(ROOT / "artifacts" / "network-sse-result.json"),
    ]
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError((stderr or stdout).decode("utf-8", errors="replace")[-4000:])
    evidence = json.loads(
        (ROOT / "artifacts" / "network-sse-result.json").read_text(encoding="utf-8")
    )
    assert evidence.get("status") == "passed", evidence
    return _record(
        "cross_network_sse",
        "network_transport",
        ["run_network_sse.py", "remote_client_disconnect_reconnect"],
        "passed",
        evidence={"remote_url": remote_url, **evidence},
    )


async def _official_pair_case(official_url: str, graphharbor_url: str) -> dict[str, Any]:
    output_path = ROOT / "artifacts" / "official-langgraph-dev-comparison.json"
    exclusions = json.loads(
        (ROOT / "docs" / "compatibility-exclusions.json").read_text(encoding="utf-8")
    )
    command = [
        sys.executable,
        str(ROOT / "scripts" / "compare_official_protocol.py"),
        "--official-url",
        official_url,
        "--graphharbor-url",
        graphharbor_url,
        "--scenario",
        str(ROOT / "tests" / "javascript" / "fixtures" / "official-protocol-scenario.json"),
        "--result-out",
        str(output_path),
    ]
    for path in [
        *exclusions.get("official_openapi_paths", []),
        *exclusions.get("graphharbor_openapi_paths", []),
    ]:
        command.extend(["--ignore-openapi-path", path])
    for method in exclusions.get("openapi_methods", []):
        command.extend(["--ignore-openapi-method", method])
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        detail = (stderr or stdout).decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"langgraph dev pair comparison failed: {detail}")
    comparison = json.loads(output_path.read_text(encoding="utf-8"))
    assert comparison.get("status") == "passed", comparison
    return _record(
        "langgraph_dev_pair_comparison",
        "official_pair",
        ["compare_official_protocol", "official_protocol_scenario"],
        "passed",
        evidence={
            "official_url": official_url,
            "scenario": "tests/javascript/fixtures/official-protocol-scenario.json",
            "difference_count": len(comparison.get("differences", [])),
        },
    )


async def _official_p0_pair_case(official_url: str, graphharbor_url: str) -> dict[str, Any]:
    output_path = ROOT / "artifacts" / "official-langgraph-dev-p0-comparison.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "compare_p0_graphs.py"),
        "--official-url",
        official_url,
        "--graphharbor-url",
        graphharbor_url,
        "--result-out",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode:
        detail = (stderr or stdout).decode("utf-8", errors="replace")[-4000:]
        raise RuntimeError(f"langgraph dev P0 pair comparison failed: {detail}")
    comparison = json.loads(output_path.read_text(encoding="utf-8"))
    assert comparison.get("status") == "passed", comparison
    return _record(
        "langgraph_dev_p0_pair_comparison",
        "official_pair",
        ["compare_p0_graphs", "p0_graph_scenarios"],
        "passed",
        evidence={
            "official_url": official_url,
            "graphs": sorted(comparison.get("graphs", {})),
        },
    )


async def _run(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.with_chat or args.with_agents or args.with_p0:
        _load_env_file(Path.home() / ".my_best" / ".env")
    results: list[dict[str, Any]] = []
    try:
        health = await _health(args.base_url)
        results.append(
            _record(
                "runtime_health", "deterministic_protocol", ["health"], "passed", evidence=health
            )
        )
    except Exception as exc:
        results.append(
            _record(
                "runtime_health",
                "deterministic_protocol",
                ["health"],
                "failed",
                failure=_safe_failure(exc),
            )
        )
        return results
    client = get_client(url=args.base_url, timeout=args.timeout)
    cases: list[tuple[str, Callable[[Any], Awaitable[dict[str, Any]]]]] = [
        ("basic", _basic_case),
        ("subgraph", _subgraph_case),
        ("hitl", lambda c: _hitl_case(c, args.base_url)),
        ("tool", _tool_case),
        ("inprocess_streaming", _inprocess_streaming_case),
        ("store", _store_case),
    ]
    if args.with_chat:
        cases.append(("chat", _chat_case))
    if args.with_agents:
        cases.extend(
            [
                (
                    "langchain_agent",
                    lambda c: _agent_case(c, "langchain_agent", "langchain_agent_loop"),
                ),
                ("deep_agent", lambda c: _agent_case(c, "deep_agent", "deep_agent_loop")),
            ]
        )
    if args.with_p0:
        cases.extend(
            [
                ("p0_supervisor", _p0_supervisor_case),
                ("p0_handoff", lambda c: _p0_handoff_case(c, args.base_url)),
                (
                    "p0_test_case_agent",
                    lambda c: _p0_agent_case(
                        c,
                        "test_case_agent",
                        "p0_test_case_agent",
                        "PASS",
                        "Create test cases for checkout project with attachment checkout.png. "
                        "Follow the required tool workflow and answer concisely.",
                        ("read_project_scope", "fetch_requirements", "run_validation"),
                    ),
                ),
                (
                    "p0_personal_assistant",
                    lambda c: _p0_personal_assistant_case(c, args.base_url),
                ),
                (
                    "p0_deepagent_demo",
                    lambda c: _p0_agent_case(
                        c,
                        "deepagent_demo",
                        "p0_deepagent_demo",
                        "PostgreSQL",
                        "Research GraphHarbor architecture. Follow the required workflow, delegate one fact lookup, and answer concisely.",
                        ("write_todos", "task", "write_todos"),
                        True,
                        "subsequence",
                    ),
                ),
            ]
        )
    if args.with_mcp:
        cases.extend(
            [
                ("mcp", _mcp_case),
                ("mcp_transport", lambda c: _mcp_transport_case(args.base_url)),
            ]
        )
    if args.with_network_sse:
        cases.append(("network_sse", _network_sse_case))
    for name, case in cases:
        try:
            results.append(await case(client))
        except Exception as exc:
            blocked = name in {
                "chat",
                "langchain_agent",
                "deep_agent",
                "p0_test_case_agent",
                "p0_personal_assistant",
                "p0_deepagent_demo",
                "mcp",
                "mcp_transport",
            } and _external_dependency_failure(exc)
            results.append(
                _record(
                    {
                        "basic": "basic_run_persistence",
                        "subgraph": "subgraph_namespace_v3",
                        "hitl": "hitl_interrupt_resume",
                        "tool": "tool_call_state",
                        "inprocess_streaming": "inprocess_streaming_all_modes",
                        "store": "store_lifecycle",
                        "chat": "real_deepseek_agent",
                        "langchain_agent": "langchain_agent_loop",
                        "deep_agent": "deep_agent_loop",
                        "p0_supervisor": "p0_assistant_supervisor",
                        "p0_handoff": "p0_customer_support_handoff",
                        "p0_test_case_agent": "p0_test_case_agent",
                        "p0_personal_assistant": "p0_personal_assistant",
                        "p0_deepagent_demo": "p0_deepagent_demo",
                        "mcp": "mcp_external_tool_call",
                        "mcp_transport": "mcp_graphharbor_transport",
                        "network_sse": "cross_network_sse_fixture",
                    }[name],
                    "real_provider"
                    if name
                    in {
                        "chat",
                        "langchain_agent",
                        "deep_agent",
                        "p0_test_case_agent",
                        "p0_personal_assistant",
                        "p0_deepagent_demo",
                    }
                    else "external_integration"
                    if name in {"mcp", "mcp_transport"}
                    else "deterministic_protocol",
                    [f"{name}_acceptance"],
                    "blocked_external_dependency" if blocked else "failed",
                    failure=_safe_failure(exc),
                )
            )
    try:
        results.append(await _replay_case(client, args.base_url))
    except Exception as exc:
        results.append(
            _record(
                "sse_replay_cursor",
                "deterministic_protocol",
                ["rest_last_event_id"],
                "failed",
                failure=_safe_failure(exc),
            )
        )
    if args.with_javascript:
        try:
            results.append(await _javascript_case(args.base_url))
        except Exception as exc:
            results.append(
                _record(
                    "javascript_sdk_contract",
                    "deterministic_protocol",
                    ["javascript_contract"],
                    "failed",
                    failure=_safe_failure(exc),
                )
            )
    if args.cross_network_sse_url:
        try:
            results.append(await _cross_network_case(args.cross_network_sse_url))
        except Exception as exc:
            results.append(
                _record(
                    "cross_network_sse",
                    "network_transport",
                    ["run_network_sse.py", "remote_client_disconnect_reconnect"],
                    "blocked_external_dependency"
                    if _external_dependency_failure(exc)
                    else "failed",
                    failure=_safe_failure(exc),
                )
            )
    if args.official_url:
        try:
            results.append(await _official_pair_case(args.official_url, args.base_url))
        except Exception as exc:
            results.append(
                _record(
                    "langgraph_dev_pair_comparison",
                    "official_pair",
                    ["compare_official_protocol", "official_protocol_scenario"],
                    "blocked_external_dependency"
                    if _external_dependency_failure(exc)
                    else "failed",
                    failure=_safe_failure(exc),
                )
            )
        if args.with_p0:
            try:
                results.append(await _official_p0_pair_case(args.official_url, args.base_url))
            except Exception as exc:
                results.append(
                    _record(
                        "langgraph_dev_p0_pair_comparison",
                        "official_pair",
                        ["compare_p0_graphs", "p0_graph_scenarios"],
                        "blocked_external_dependency"
                        if _external_dependency_failure(exc)
                        else "failed",
                        failure=_safe_failure(exc),
                    )
                )
    elif args.require_official:
        results.append(
            _record(
                "langgraph_dev_pair_comparison",
                "official_pair",
                ["compare_official_protocol", "official_protocol_scenario"],
                "not_run",
                failure="--require-official was set but --official-url was not provided",
            )
        )
    await client.aclose()
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url", default=os.environ.get("GRAPHHARBOR_URL", "http://127.0.0.1:31296")
    )
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--with-chat", action="store_true")
    parser.add_argument("--with-agents", action="store_true")
    parser.add_argument("--with-javascript", action="store_true")
    parser.add_argument(
        "--with-p0", action="store_true", help="run the five complex P0 fixture graphs"
    )
    parser.add_argument(
        "--with-mcp", action="store_true", help="run the external MCP fixture graph"
    )
    parser.add_argument(
        "--with-network-sse", action="store_true", help="run the multi-phase SSE fixture"
    )
    parser.add_argument(
        "--cross-network-sse-url",
        help="run disconnect/reconnect against a separately hosted GraphHarbor URL",
    )
    parser.add_argument(
        "--official-url",
        default=os.environ.get("OFFICIAL_LANGGRAPH_DEV_URL"),
        help="base URL of the pinned langgraph dev reference service",
    )
    parser.add_argument(
        "--require-official",
        action="store_true",
        help="fail when the paired langgraph dev comparison is not run",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="non-gating mode: allow blocked or not_run external cases",
    )
    args = parser.parse_args()
    results = asyncio.run(_run(args))
    output = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": args.base_url,
        "with_chat": args.with_chat,
        "with_agents": args.with_agents,
        "with_javascript": args.with_javascript,
        "with_p0": args.with_p0,
        "with_mcp": args.with_mcp,
        "with_network_sse": args.with_network_sse,
        "cross_network_sse_url": args.cross_network_sse_url,
        "results": results,
    }
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    passed = sum(item["status"] == "passed" for item in results)
    import logging

    logging.getLogger(__name__).info(
        "acceptance: %s/%s passed; result=%s", passed, len(results), ARTIFACT
    )
    allowed = (
        {"passed", "blocked_external_dependency", "not_run"}
        if args.allow_incomplete
        else {"passed"}
    )
    return 0 if all(item["status"] in allowed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
