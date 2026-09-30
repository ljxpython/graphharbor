"""Wire requirements consumed by the official framework SDKs."""

import pytest

from langgraph_runtime_pg.protocol import protocol_event


def test_run_stream_subgraphs_false_excludes_target_scope_lifecycle():
    from langhost.streaming import _event_frame

    event = {
        "seq": 3,
        "event": {
            "event": "lifecycle",
            "namespace": [],
            "data": {
                "event": "started",
                "namespace": ["child:1"],
            },
        },
    }
    assert _event_frame(event, modes={"events"}, stream_subgraphs=False, version="v3") is None
    assert _event_frame(event, modes={"events"}, stream_subgraphs=True, version="v3") is not None


@pytest.mark.parametrize("phase", ["started", "running", "completed", "failed", "interrupted"])
def test_lifecycle_scope_fields_and_timestamp_survive_replay(phase):
    from langhost.protocol_api import _wire_matches

    data = {
        "event": phase,
        "namespace": ["child:1", "nested:2"],
        "graph_name": "named-child",
        "error": "fixture error",
        "cause": {"type": "future-cause", "source": "parent"},
    }
    source = {"event": "lifecycle", "namespace": [], "data": data, "timestamp": 1234}
    first = protocol_event(event_id="e", sequence=7, run_id="r", thread_id="t", event=source)
    second = protocol_event(event_id="e", sequence=7, run_id="r", thread_id="t", event=source)
    assert first == second
    assert first["params"]["namespace"] == data["namespace"]
    assert first["params"]["data"] == data
    assert first["params"]["timestamp"] == 1234
    assert _wire_matches(first, {"channels": ["lifecycle"], "namespaces": [["child:1"]]})
    assert not _wire_matches(first, {"channels": ["lifecycle"], "namespaces": [["other:1"]]})


def test_root_lifecycle_matches_official_running_and_error_shape():
    source = {"event": "lifecycle", "status": "running", "graph_name": "registered-graph"}
    wire = protocol_event(event_id="e", sequence=1, run_id="r", thread_id="t", event=source)
    assert wire["params"]["data"]["event"] == "running"
    assert wire["params"]["data"]["graph_name"] == "registered-graph"
    source.update(status="error", error={"type": "ValueError", "message": "fixture error"})
    wire = protocol_event(event_id="e", sequence=2, run_id="r", thread_id="t", event=source)
    assert wire["params"]["data"]["event"] == "failed"
    assert wire["params"]["data"]["error"] == "fixture error"


def test_message_envelope_preserves_checkpoint_id_and_scoped_node():
    event = protocol_event(
        event_id="event-1",
        sequence=1,
        run_id="run-1",
        thread_id="thread-1",
        event={
            "event": "messages",
            "namespace": ["child:1"],
            "data": [
                {"event": "message-start", "id": "lc_run--model-1", "role": "ai"},
                {"run_id": "model-1", "langgraph_node": "model"},
            ],
        },
    )
    assert event["type"] == "event"
    assert event["params"]["data"]["id"] == "lc_run--model-1"
    assert event["params"]["node"] == "model"
    assert event["params"]["namespace"] == ["child:1"]


def test_interrupt_and_nested_lifecycle_payloads_are_sdk_readable():
    requested = protocol_event(
        event_id="event-2",
        sequence=2,
        run_id="run-1",
        thread_id="thread-1",
        event={
            "event": "input.requested",
            "data": {"interrupt_id": "review-1", "value": {"question": "Proceed?"}},
        },
    )
    assert requested["params"]["data"]["payload"] == {"question": "Proceed?"}
    completed = protocol_event(
        event_id="event-3",
        sequence=3,
        run_id="run-1",
        thread_id="thread-1",
        event={"event": "lifecycle", "namespace": ["child:1"], "data": {"event": "completed"}},
    )
    assert completed["params"]["data"]["event"] == "completed"
