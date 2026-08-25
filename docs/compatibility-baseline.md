# GraphHarbor Compatibility Baseline

基线日期：2026-08-25  
对应 OpenSpec change：`implement-production-agent-server`

本文记录“当前事实”和“冻结目标”之间的差异。当前版本通过 GraphHarbor workspace、`ai-agent-platform` 的 adapter、官方 SDK 签名和 LangChain Agent Server 文档交叉确认。未标记为已验证的能力不能对外宣称兼容。

## 1. Version Matrix

| Component | Current lock/runtime | Frozen target | Status |
|---|---:|---:|---|
| Python | workspace `>=3.11` | 3.13 production, CI 3.11/3.12/3.13 | regression passed |
| `langgraph` | 1.2.11 | 1.2.11 | lock verified |
| `langgraph-sdk` | 0.4.3 | 0.4.3 | lock verified |
| `langgraph-cli` | 0.4.31 | 0.4.31 | lock verified |
| `langgraph-api` | 0.13.0 compatibility spike only | 0.13.0 spike only | not a production dependency |
| `langgraph-checkpoint` | 4.2.0 | 4.2.0 | lock verified |
| `langgraph-checkpoint-postgres` | 3.1.2 | 3.1.2 | lock verified |
| `langgraph-runtime-inmem` | 0.33.0 comparison only | 0.33.0 comparison only | not production source of truth |
| PostgreSQL | 16.9 CI / host service | PostgreSQL 16 baseline | CI image and host service verified |
| Redis | 7.4.2 CI / host service | Redis 7 baseline | CI image and host service verified |

The target values are pinned in the production plan. `langgraph-api==0.13.0` is allowed only in an internal comparison environment; the final GraphHarbor server must start without `LANGGRAPH_CLOUD_LICENSE_KEY` and without a LangSmith hosted service.

## 2. Current Call Surface

### platform-api SDK adapter

| Resource | SDK methods currently exposed | Compatibility target |
|---|---|---|
| Assistants | `get`, `create`, `update`, `delete` | Core |
| Threads | `get`, `create`, `search`, `count`, `prune`, `update`, `delete`, `copy` | Core |
| Thread state | `get_state`, `update_state`, `get_history` | Core |
| Runs | `create`, `stream`, `wait`, `get`, `list`, `delete`, `join`, `join_stream` | Core |
| Run control | `create_batch`, `cancel`, `cancel_many` | Core |
| Cron | global and thread `create`, `search`, `count`, `update`, `delete` | Core |

Evidence:

- `apps/platform-api/app/adapters/langgraph/runs_sdk_adapter.py`
- `apps/platform-api/app/adapters/langgraph/threads_sdk_adapter.py`
- `apps/platform-api/app/adapters/langgraph/assistants_client.py`
- `apps/platform-api/app/adapters/langgraph/runtime_gateway_upstream.py`

### Frontend and runtime-service

| Caller | Current behavior | Gap |
|---|---|---|
| `platform-web` / `runtime-web` | Calls `client.runs.stream(...)` with `values`, `updates`, `tasks`, `streamSubgraphs`, `streamResumable` and `onDisconnect` | Does not explicitly pass `version="v2"` |
| platform-api stream adapter | Converts SDK events to SSE and forwards a fixed allowlist of fields | `version` is absent from `_STREAM_FIELDS` |
| runtime-service config | Uses standard `langgraph.json` with 9 registered graphs, `platform_auth` and custom app | Must load unchanged under GraphHarbor server |
| custom routes | `/internal/capabilities/models`, `/internal/capabilities/tools` | Must share Agent Server Principal/lifespan |

The v2 stream target is therefore a required implementation change, not a currently verified behavior.

## 3. Target Public Protocol Surface

The following behavior is the contract to verify against the target official SDK/OpenAPI:

| Surface | Required behavior | Evidence gate |
|---|---|---|
| Discovery | `GET /ok`, `GET /info`, `GET /openapi.json`, graph/assistant discovery | REST + Python/JS SDK |
| Assistants | get/create/update/delete and standard response/error models | REST + SDK |
| Threads | CRUD/search/count/copy/prune, state/history/update-state, thread-scoped stream/reconnect | REST + SDK + SSE |
| Runs | create/get/list/delete/wait/join/stream | REST + SDK |
| Run control | batch create, `/runs/cancel`, single cancel, `wait`, `interrupt`, `rollback` | REST + SDK |
| Cron | global/thread create/search/count/update/delete | REST + SDK |
| Store | item put/get/search/namespaces/delete with PostgreSQL persistence | REST + Python/JavaScript SDK |
| Remote stream v2 | `client.runs.stream(..., version="v2")`, modes, subgraphs, heartbeat, terminal event, replay | Real SSE decode |
| Protocol v2 events | thread event stream, channel/namespace filters, `since`, command envelope | Real SSE + command tests |
| Graph v3 | `stream_events(version="v3")`, typed projections, lifecycle and subgraph namespace | Graph + remote projection tests |
| HITL | `interrupt(payload)` and `Command(resume=...)` on same thread/checkpoint | Restart/duplicate resume tests |

Official references used for the contract:

- https://docs.langchain.com/oss/python/langgraph/event-streaming
- https://docs.langchain.com/oss/python/langgraph/interrupts
- https://docs.langchain.com/langsmith/agent-server-api/streaming/protocol-v2-event-stream-sse
- https://docs.langchain.com/langsmith/agent-server-api/streaming/protocol-v2-command
- https://docs.langchain.com/langsmith/agent-server-api/thread-runs/create-background-run
- https://docs.langchain.com/langsmith/agent-server-api/thread-runs/cancel-runs

## 4. Auth Baseline

Production integration uses a short-lived delegation JWT issued by platform-api. The normalized Principal contains:

```text
identity, tenant_id, project_id, roles, scopes,
credential_type, delegation_id/jti, issuer, audience, issued_at, expires_at
```

Current `platform_auth` validates `sub`, tenant/project, role, `jti`, permissions, issuer, audience, algorithm and required claims. `auth/provider.py` also contains demo tokens/API keys and Supabase OAuth; those remain development/standalone profiles and are not the production integration default.

Required tests: valid token, expired token, wrong issuer/audience, unknown key id, key rotation, management credential isolation, client identity override, cross-tenant 404 and custom-route authorization.

## 5. Run and Persistence Baseline

Public statuses:

```text
pending -> running -> success | error | timeout | interrupted
```

Cancellation and HITL both expose `interrupted`; internal reasons distinguish `cancel_requested`, `hitl_interrupt`, `shutdown_requeue`, `multitask_interrupt`, `timeout` and `retry_exhausted`. PostgreSQL is the durable source of truth. Redis provides queue, Pub/Sub, cancel transport and bounded replay only.

GraphHarbor now owns the API/worker lifecycle; PostgreSQL operations, checkpoint setup, Redis fanout/heartbeat and Alembic migrations are validated by the self-owned server contract suites. Package release requires the three-version and TestPyPI gates recorded in the release runbook.

## 6. Baseline Acceptance

The baseline is complete only when all of the following are recorded as command/test evidence:

1. Target lockfile versions resolve on Python 3.11, 3.12 and 3.13.
2. A no-License-Key server starts locally with host PostgreSQL and Redis.
3. `/ok`, `/info`, `/openapi.json` and graph discovery succeed.
4. One P0 graph completes a persisted run through the official Python SDK.
5. Current custom routes and auth remain reachable with the same Principal contract.
6. The stream path explicitly passes and verifies `version="v2"`.

Until those checks pass, the profile remains `experimental`.
