"""Lifecycle fixtures exercise the native engine, not injected event dictionaries."""

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from v3_graphs import child_builder, send_graph, tool_graph


def test_fanout_and_tool_cause_are_real_and_distinct():
    async def check():
        assert sorted((await send_graph.ainvoke({}))["results"]) == ["alpha", "beta"]
        stream = await tool_graph.astream_events({"messages": []}, version="v3")
        async with stream:
            events = [e["params"]["data"] async for e in stream if e["method"] == "lifecycle"]
        started = [e for e in events if e["event"] in {"started", "running"}]
        assert len(started) == 2
        assert len({tuple(e["namespace"]) for e in started}) == 2
        assert {e["cause"]["tool_call_id"] for e in started} == {"dispatch-alpha", "dispatch-beta"}
        assert len([e for e in events if e["event"] == "completed"]) == 2

    asyncio.run(check())


def test_failure_and_interrupt_resume():
    graph = child_builder.compile(checkpointer=InMemorySaver())
    with pytest.raises(ValueError, match="v3 fixture failure"):
        graph.invoke({"scenario": "failed"}, {"configurable": {"thread_id": "failure"}})
    config = {"configurable": {"thread_id": "resume"}}
    result = graph.invoke({"scenario": "interrupted"}, config)
    assert result["__interrupt__"]
    assert graph.invoke(Command(resume=True), config)["results"] == ["done:True"]


def test_comparison_preserves_cause_and_scope_identity():
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "v3_comparison", Path(__file__).parents[2] / "scripts/compare_official_protocol.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def report(cause):
        return {
            "scenarios": [
                {
                    "graph": "g",
                    "scenario": "done",
                    "events": [
                        {
                            "params": {
                                "namespace": ["child:00000000-0000-4000-8000-000000000001"],
                                "data": {"event": "started", "cause": cause},
                            }
                        }
                    ],
                }
            ]
        }

    assert not module.compare_lifecycle_reports(
        report({"type": "toolCall", "tool_call_id": "a"}),
        report({"type": "toolCall", "tool_call_id": "a"}),
    )
    assert module.compare_lifecycle_reports(
        report({"type": "toolCall", "tool_call_id": "a"}),
        report({"type": "toolCall", "tool_call_id": "b"}),
    )
