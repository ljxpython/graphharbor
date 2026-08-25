## Context

GraphHarbor currently provides a PostgreSQL/Redis runtime fork coupled to private `langgraph-api` interfaces. The target system must preserve the current `langgraph.json`, graphs, custom routes and auth while exposing official Agent Server wire behavior without LangSmith hosting or a cloud license key. The affected boundary spans the CLI, API protocol, runtime persistence, workers, authentication and the ai-agent-platform adapters.

## Goals / Non-Goals

**Goals:**

- Provide a self-hosted Agent Server boundary consumable by the official Python and JavaScript SDKs.
- Preserve Core REST/SSE behavior for assistants, threads, runs, cron, v2 streaming, Protocol v2 events, v3 projections, subgraphs and HITL.
- Make PostgreSQL the durable source of truth and Redis the queue/pub/sub/replay transport.
- Support local process deployment with independent PostgreSQL and Redis services.
- Enforce one Principal contract for Agent Server and custom routes.

**Non-Goals:**

- Reimplement or depend on LangSmith hosted services.
- Include `store`, MCP, A2A, webhooks, Generative UI or multi-region HA in the first Core profile.
- Preserve private `langgraph-api` internals as a public extension point.
- Change existing graph business logic unless a graph violates the frozen runtime contract.

## Decisions

### 1. Self-owned protocol boundary

GraphHarbor will own the FastAPI/ASGI Agent Server boundary and use public SDK/OpenAPI behavior as the contract. `langgraph-api==0.13.0` may run only in an internal comparison harness. This removes the license-gated dependency while keeping the standard `langgraph.json` and SDK surface.

Alternative rejected: keeping `langgraph-api` as the production HTTP server. It preserves compatibility cheaply but retains the license/private-internal risk that this change is intended to remove.

### 2. Vertical slices

Implement in slices: baseline/config, persistence, run execution, auth/lifespan, v2/v3 streaming, P0 graph E2E, multi-worker recovery, deployment. Each slice must pass official SDK/REST tests before the next slice expands scope.

Alternative rejected: implementing all endpoints first and testing later. That would hide protocol and persistence mismatches until the end.

### 3. Persistence and execution

PostgreSQL stores assistants, threads, runs, run status/reason, checkpoints, leases and migration metadata. Redis carries jobs, cancel signals, pub/sub events and bounded replay. A worker claims a run using a PostgreSQL lease, renews heartbeat, emits sequenced events, and is reclaimed by a reaper after lease expiry.

The public run status is `pending`, `running`, `success`, `error`, `timeout` or `interrupted`. Cancellation and HITL both map to `interrupted`; an internal reason distinguishes them. Infrastructure failures retry at most three times with bounded exponential backoff.

Alternative rejected: Redis as the source of truth. Redis loss must not erase run state or checkpoints.

### 4. Authentication and lifespan

Production integration accepts short-lived delegation JWTs issued by platform-api. Runtime validates signature and claims locally, creates a normalized Principal, and passes it to Agent Server and custom routes. `tenant_id` and `project_id` come from the Principal, never request payloads. Cross-tenant resources return 404.

Startup composes server, GraphHarbor runtime and user application lifespans in that order; shutdown marks readiness false, drains/requeues work, then closes custom resources and runtime pools. Schema migration is an explicit command/job, not an implicit startup side effect.

### 5. Streaming and replay

Every event receives a monotonically increasing run/thread sequence and namespace/path metadata. v2 run streams remain available; Protocol v2 thread event streams support commands and `since` replay; v3 graph event projections expose typed lifecycle, messages, values, updates, custom, tools and subgraph events. Replay is bounded and terminal PostgreSQL state is the fallback when the cursor is older than the buffer.

### 6. Deployment

The acceptance path runs API, worker and migration as local processes against isolated host PostgreSQL/Redis. Docker Compose and Kubernetes are out of scope.

## Risks / Trade-offs

- **Public protocol drift** -> pin the compatibility matrix and run SDK/OpenAPI contract tests for every dependency upgrade.
- **Exactly-once side effects are impossible for arbitrary tools** -> persist run/checkpoint transitions, expose idempotency keys, and require tool-side idempotency for retried effects.
- **Replay buffer loss** -> persist terminal status/checkpoints in PostgreSQL and return an explicit cursor-too-old error when replay cannot be completed.
- **Custom app lifespan conflicts** -> use one composed lifespan and add startup/shutdown ordering tests.
- **JWT key rotation or clock skew** -> cache JWKS with expiry, allow bounded clock skew, and fail closed on unknown key ids.
- **P0 graphs depend on external services** -> separate protocol/runtime tests from graph tests and provide controlled fakes where real dependencies are not deterministic.

## Migration Plan

1. Establish the version, call-surface and OpenAPI baseline.
2. Run the current runtime against the target LangGraph versions in an internal spike.
3. Add GraphHarbor schema migrations and local API/worker/migration commands.
4. Enable Core protocol slices behind a compatibility profile/capability probe.
5. Run P0 graph, SDK, multi-worker and fault suites locally.
6. Roll out behind a new API base URL; retain the existing runtime as rollback until PostgreSQL, stream and HITL evidence passes.
7. Publish only after local security and reliability gates pass.

Rollback stops new runs on GraphHarbor, drains/requeues pending work, and switches clients back to the previous runtime. PostgreSQL migrations must be additive or have a tested down/forward recovery procedure; no destructive migration is allowed in the first rollout.

## Open Questions

- Production capacity targets, data retention and legacy thread/checkpoint migration volume are release-gate inputs, not blockers for the initial implementation slices.
- The observability backend is deployment-specific; the implementation must emit structured logs and Prometheus/OTel-compatible signals without requiring a hosted vendor.
