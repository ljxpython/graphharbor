from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "compare_official_protocol.py"
    spec = importlib.util.spec_from_file_location("official_protocol_compare", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compare_accepts_equal_output_and_dynamic_values() -> None:
    compare = _module()
    official = compare.Response(
        201,
        {"content-type": "application/json"},
        {
            "run_id": "00000000-0000-4000-8000-000000000001",
            "created_at": "2026-08-25T12:00:00Z",
        },
    )
    graphharbor = compare.Response(
        201,
        {"content-type": "application/json"},
        {
            "run_id": "00000000-0000-4000-8000-000000000002",
            "created_at": "2026-08-25T12:01:00Z",
        },
    )
    assert compare.compare(official, graphharbor, path="/runs") == []


def test_compare_normalizes_official_uuidv7_values() -> None:
    compare = _module()

    assert compare.normalize("0198e716-0801-7000-8000-000000000001") == "<uuid>"


def test_compare_reports_json_and_header_mismatches() -> None:
    compare = _module()
    official = compare.Response(200, {"content-type": "application/json"}, {"status": "success"})
    graphharbor = compare.Response(201, {"content-type": "text/plain"}, {"status": "error"})

    differences = compare.compare(official, graphharbor, path="/runs")

    assert {item.path for item in differences} == {
        "/runs.status",
        "/runs.headers.content-type",
        "/runs.body.status",
    }


def test_compare_openapi_reports_missing_official_path() -> None:
    compare = _module()
    official = compare.Response(200, {}, {"paths": {"/ok": {"get": {}}, "/runs": {"post": {}}}})
    graphharbor = compare.Response(200, {}, {"paths": {"/ok": {"get": {}}}})

    differences = compare._compare_openapi(official, graphharbor, set())

    assert differences[0].path == "$.openapi.paths./runs"
    assert differences[0].graphharbor == "<missing>"


def test_compare_openapi_uses_path_and_method_shape_only() -> None:
    compare = _module()
    official = compare.Response(
        200,
        {"content-type": "application/json"},
        {"paths": {"/runs": {"post": {"summary": "official"}}}},
    )
    graphharbor = compare.Response(
        200,
        {"content-type": "application/json"},
        {"paths": {"/runs": {"post": {"summary": "different framework metadata"}}}},
    )

    assert compare.compare(official, graphharbor, path="/openapi.json") == []
    assert compare._compare_openapi(official, graphharbor, set()) == []


def test_compare_openapi_supports_documented_method_exclusions() -> None:
    compare = _module()
    official = compare.Response(200, {}, {"paths": {"/threads": {"post": {}}}})
    graphharbor = compare.Response(200, {}, {"paths": {"/threads": {"get": {}, "post": {}}}})

    assert compare._compare_openapi(official, graphharbor, set(), {("/threads", "GET")}) == []


def test_scenario_resolves_independent_response_references(tmp_path: Path) -> None:
    compare = _module()
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        '[{"name":"assistant","method":"POST","path":"/assistants"},'
        '{"name":"lookup","method":"GET","path":"/assistants/{{assistant.assistant_id}}"}]',
        encoding="utf-8",
    )

    steps = compare._load_scenario(str(scenario))

    assert compare._resolve_references(steps[1]["path"], {"assistant": {"assistant_id": "one"}}) == "/assistants/one"
    assert compare._resolve_references(steps[1]["path"], {"assistant": {"assistant_id": "two"}}) == "/assistants/two"


def test_compare_sse_keeps_events_but_normalizes_dynamic_values() -> None:
    compare = _module()
    official = compare.Response(
        200,
        {"content-type": "text/event-stream"},
        'id: 1\nevent: values\ndata: {"run_id":"00000000-0000-4000-8000-000000000001"}\n\n',
    )
    graphharbor = compare.Response(
        200,
        {"content-type": "text/event-stream"},
        'id: 9\nevent: values\ndata: {"run_id":"00000000-0000-4000-8000-000000000002"}\n\n',
    )

    assert compare.compare(official, graphharbor, path="/runs/stream") == []


def test_compare_sse_treats_crlf_and_lf_as_the_same_frame_separator() -> None:
    compare = _module()
    official = compare.Response(
        200,
        {"content-type": "text/event-stream"},
        'event: values\r\ndata: {"value":1}\r\n\r\n',
    )
    graphharbor = compare.Response(
        200,
        {"content-type": "text/event-stream"},
        'event: values\ndata: {"value":1}\n\n',
    )

    assert compare.compare(official, graphharbor, path="/runs/stream") == []


def test_scenario_stream_uses_triggered_capture(monkeypatch, tmp_path: Path) -> None:
    compare = _module()
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        '[{"name":"thread","method":"POST","path":"/threads"},'
        '{"name":"stream","method":"GET","path":"/threads/{{thread.thread_id}}/stream",'
        '"headers":{"last-event-id":"-"},"stream":{"frames":1,'
        '"trigger":{"method":"POST","path":"/threads/{{thread.thread_id}}/runs",'
        '"body":{"input":{}}}}}]',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    def request(*_args, **_kwargs):
        return compare.Response(200, {"content-type": "application/json"}, {"thread_id": "one"})

    def triggered(**kwargs):
        captured.update(kwargs)
        response = compare.Response(200, {"content-type": "text/event-stream"}, "event: values\n\n")
        return response, response

    monkeypatch.setattr(compare, "_request", request)
    monkeypatch.setattr(compare, "_triggered_streams", triggered)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "compare",
            "--official-url",
            "http://official",
            "--graphharbor-url",
            "http://graphharbor",
            "--scenario",
            str(scenario),
        ],
    )

    assert compare.main() == 0
    assert captured["step"] == compare._load_scenario(str(scenario))[1]
