"""HTTP trust-boundary tests; transactions live with the PostgreSQL runtime."""

import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from langgraph_sdk import Auth

from langgraph_runtime_pg.run_acceptance import AcceptanceGrant, request_digest
from langhost import server


def grant_for(body, key="retry", **changes):
    return {
        "scope_id": "a" * 64,
        "credential_id": "b" * 64,
        "operation": "create",
        "thread_id": str(uuid4()),
        "key_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "request_digest": request_digest(body),
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":1e999}',
        b"[]",
        b"\xff",
        b'{"idempotency_key":"x"}',
        b'{"runtime_context":{}}',
        b'{"__graphharbor_runtime_context":{}}',
        b'{"if_not_exists":"create"}',
    ],
)
async def test_invalid_body_never_reaches_creation(monkeypatch, body):
    from langhost import core_api

    grant = grant_for(body)
    auth = Auth()

    @auth.authenticate
    async def authenticate():
        return {"identity": "alice", "run_acceptance": grant}

    async def fail(*args, **kwargs):
        pytest.fail("invalid body reached creation")

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    monkeypatch.setattr(core_api, "runs_create", fail)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            f"/threads/{grant['thread_id']}/runs/acceptance",
            content=body,
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": "retry",
                "GraphHarbor-Request-Digest": grant["request_digest"],
            },
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_fixed_read_grant_cannot_access_other_resources(monkeypatch):
    grant = grant_for(b"{}", operation="read")
    auth = Auth()

    @auth.authenticate
    async def authenticate():
        return {"identity": "alice", "run_acceptance": grant}

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    path = f"/threads/{grant['thread_id']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        for method, target in [
            ("POST", path + "/runs/acceptance"),
            ("GET", path + "/state"),
            ("GET", path + "/runs"),
            ("GET", "/store/items"),
            ("POST", path + "/runs/cancel-active"),
            ("GET", "/mcp/"),
        ]:
            assert (await client.request(method, target)).status_code == 403


@pytest.mark.asyncio
async def test_anonymous_and_auth_without_handler_fail_closed(monkeypatch):
    from starlette.exceptions import HTTPException

    from langgraph_runtime_pg.authorization import authorize

    for auth, user in [(None, None), (Auth(), {"identity": "alice"})]:
        with pytest.raises(HTTPException) as error:
            await authorize(auth, user, "threads", "read", {}, require_handler=True)
        assert error.value.status_code == 403
        assert await authorize(auth, user, "threads", "read", {}) == {}
    app = server.create_app({"graphs": {}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        assert (await client.get(f"/threads/{uuid4()}/runs/acceptance")).status_code == 403
        for target in [
            "/runs",
            "/runs/batch",
            "/runs/stream",
            "/runs/wait",
            f"/threads/{uuid4()}/commands",
        ]:
            assert (
                await client.post(
                    target, json={}, headers={"GraphHarbor-Request-Digest": request_digest(b"{}")}
                )
            ).status_code == 422


def test_byte_digest_and_stable_binding():
    vectors = [
        (
            '{"text":"中文","n":1,"nil":null}',
            "6db3069d7c2fe82edae11f60cd1614245665f0107c4121ca8eca937a9797df98",
        ),
        (
            r'{"text":"\u4e2d\u6587","n":1,"nil":null}',
            "8efd6e7d220355cbab25e808f90666873ccf65e854d126b850349de353ca6c3d",
        ),
        (
            '{ "text":"中文","n":1,"nil":null}',
            "3324088b57e3da71d4526503279e9f8dcdd095decfb016813a575ab72ee18f1b",
        ),
        (
            '{"nil":null,"n":1,"text":"中文"}',
            "15c9037795b8123d1f58ad71e55cb0fb0d6ba6790c8232eda921a5590590dcd9",
        ),
        (
            '{"text":"中文","n":1}',
            "8bb030a34a905243c60430138d8e859f8abfc6dea58e5e716c25b266d4e58240",
        ),
        (
            '{"text":"中文","n":1.0,"nil":null}',
            "501df2a15b681637935a6f87007364784d918dc609ba46bed51a4f307b0227ea",
        ),
        ('{"items":[1,2]}', "02d21651bf4a0c676159456cd47d09d4ac585db5ed1324ac191f3e98640846a3"),
        ('{"items":[2,1]}', "999e7876bd6caa905b1c5fd6e9a875224c9523c3f71d792d62de83fb5201ade1"),
    ]
    for text, expected in vectors:
        assert request_digest(text.encode()) == "sha256:" + expected
    body = '{"text":"中文","n":1,"nil":null}'.encode()
    grant = grant_for(body)
    parsed = AcceptanceGrant.parse(grant)
    assert request_digest(body) == "sha256:" + hashlib.sha256(body).hexdigest()
    for other in [json.dumps(json.loads(body)).encode(), body.replace(b"1", b"1.0"), body + b" "]:
        assert request_digest(other) != parsed.request_digest
    assert AcceptanceGrant.parse({**grant, "operation": "read"}).receipt_key == parsed.receipt_key
    assert (
        AcceptanceGrant.parse({**grant, "credential_id": "c" * 64}).receipt_key
        != parsed.receipt_key
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    [
        "duplicate-key",
        "duplicate-digest",
        "missing-digest",
        "wrong-key",
        "wrong-digest",
        "body-digest",
        "compressed",
        "query",
        "get-body",
    ],
)
async def test_invalid_headers_target_and_get_body(monkeypatch, case):
    from langhost import core_api

    grant = grant_for(b"{}")
    auth = Auth()

    @auth.authenticate
    async def authenticate():
        return {"identity": "alice", "run_acceptance": grant}

    async def fail(*args, **kwargs):
        pytest.fail("invalid target reached execution")

    monkeypatch.setattr(server, "_load_symbol", lambda *args: auth)
    monkeypatch.setattr(core_api, "runs_create", fail)
    app = server.create_app({"graphs": {}, "auth": {"path": "fixture:auth"}})
    headers = [
        ("Content-Type", "application/json"),
        ("Idempotency-Key", "retry"),
        ("GraphHarbor-Request-Digest", grant["request_digest"]),
    ]
    if case == "duplicate-key":
        headers.append(("Idempotency-Key", "retry"))
    if case == "duplicate-digest":
        headers.append(("GraphHarbor-Request-Digest", grant["request_digest"]))
    if case == "missing-digest":
        headers.pop()
    if case == "wrong-key":
        headers[1] = ("Idempotency-Key", "another")
    if case == "wrong-digest":
        headers[2] = ("GraphHarbor-Request-Digest", request_digest(b"changed"))
    if case == "compressed":
        headers.append(("Content-Encoding", "gzip"))
    target = f"/threads/{grant['thread_id']}/runs/acceptance"
    if case == "query":
        target += "?query=1"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.request(
            "GET" if case == "get-body" else "POST",
            target,
            content=b"changed" if case == "body-digest" else b"{}",
            headers=headers,
        )
        assert response.status_code == (403 if case in {"wrong-key", "wrong-digest"} else 422)


@pytest.mark.asyncio
async def test_openapi_has_static_acceptance_contract():
    app = server.create_app({"graphs": {}})
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        spec = (await client.get("/openapi.json")).json()
    path = spec["paths"]["/threads/{thread_id}/runs/acceptance"]
    assert set(path) == {"get", "post"}
    for operation in path.values():
        assert {"Idempotency-Key", "GraphHarbor-Request-Digest"} <= {
            item["name"] for item in operation["parameters"]
        }
        assert (
            operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"]
            == "no-store"
        )
