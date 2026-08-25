## 1. Baseline and compatibility contract

- [x] 1.1 Record the frozen version matrix for Python 3.11/3.12/3.13, `langgraph==1.2.11`, `langgraph-sdk==0.4.3`, `langgraph-cli==0.4.31`, checkpoint packages and the internal-only `langgraph-api==0.13.0` spike.
- [x] 1.2 Generate `docs/compatibility-baseline.md` from the current GraphHarbor and ai-agent-platform call surfaces, including endpoint, SDK method, request/response fields, Principal and test location.
- [x] 1.3 Capture target public OpenAPI/SDK behavior for Core assistants, threads, runs, batch/cancel, cron, v2 streaming and Protocol v2 events.
- [x] 1.4 Add a baseline script that verifies lockfile versions, Python matrix and the no-License-Key production profile.

## 2. Runtime package and local process entrypoints

- [x] 2.1 Remove the final production startup dependency on private `langgraph-api` modules while retaining an isolated compatibility-spike test profile.
- [x] 2.2 Implement separate local commands for migration, Agent Server API and production worker startup against host PostgreSQL/Redis.
- [x] 2.3 Add readiness/liveness checks for PostgreSQL, Redis, runtime pools, queue and graph discovery.
- [x] 2.4 Add composed server/runtime/custom-app lifespan handling with ordered startup and reverse-order shutdown.
- [x] 2.5 Add local runbook and environment example with isolated database name, Redis prefix, pool limits and timeouts.

## 3. PostgreSQL schema and migrations

- [x] 3.1 Define additive tables/columns for assistants, threads, runs, run reasons, checkpoints, leases, retry metadata, event sequence and schema version.
- [x] 3.2 Add idempotent migration creation, upgrade and schema-version checks; keep migration out of normal server startup.
- [x] 3.3 Implement transactional run claim, lease renewal, terminal transition and idempotency-key constraints.
- [x] 3.4 Add checkpoint read/write/delete behavior for HITL resume and rollback cancellation.
- [x] 3.5 Add PostgreSQL unit/integration tests for empty database, repeated migration, restart recovery, concurrent claim and rollback.

## 4. Redis queue, worker and recovery

- [x] 4.1 Implement namespaced Redis job queue, cancel channel, Pub/Sub event channel and bounded replay buffer.
- [x] 4.2 Implement worker claim, heartbeat, graceful drain/requeue and reaper recovery using the PostgreSQL lease.
- [x] 4.3 Implement infrastructure retry with maximum three attempts and bounded exponential backoff; exclude business errors, HITL waits and user cancellation.
- [x] 4.4 Implement cross-instance cancel and terminal event publication without allowing late cancel to overwrite terminal state.
- [x] 4.5 Add worker/API/Redis restart, worker-kill and multi-worker tests.

## 5. Principal and authorization

- [x] 5.1 Implement delegation JWT validation using issuer/audience/signature/expiry/claims and JWKS cache with bounded clock skew.
- [x] 5.2 Define the normalized Principal and map `sub`, tenant/project, roles/scopes, credential type and `jti`.
- [x] 5.3 Apply Principal-derived tenant/project filters to assistants, threads, runs, checkpoints and custom routes; reject client identity overrides.
- [x] 5.4 Separate management credentials from user/delegation credentials and disable demo credentials in production profile.
- [x] 5.5 Add 401/403/cross-tenant-404, key rotation and custom-route auth tests.

## 6. Core Agent Server protocol

- [x] 6.1 Implement standard config/discovery, `/ok`, `/info`, `/openapi.json`, assistants and thread endpoints.
- [x] 6.2 Implement thread state/history/count/copy/prune/update-state behavior and official validation/errors.
- [x] 6.3 Implement run create/get/list/delete/wait/join, batch create, single cancel and bulk cancel with official fields.
- [x] 6.4 Implement cron create/search/count/update/delete for global and thread scopes.
- [x] 6.5 Add official Python SDK, JavaScript SDK and REST contract tests for every Core endpoint.
- [x] 6.6 Return explicit capability/error responses for Extended `store` and other unsupported capabilities.

## 7. Streaming, subgraphs and HITL

- [x] 7.1 Forward and validate explicit `version="v2"` in the current platform-api and runtime-web stream path.
- [x] 7.2 Implement v2 run SSE modes, heartbeat, terminal events, `stream_subgraphs` and replay cursor behavior.
- [x] 7.3 Implement Protocol v2 thread event stream, channel/namespace filters, `since` replay and command envelopes.
- [x] 7.4 Implement v3 typed projections for messages, values, updates, custom, tools, lifecycle and subgraph events.
- [x] 7.5 Implement interrupt payload persistence, `Command(resume=...)`, duplicate-resume idempotency and cancel/HITL reason projection.
- [x] 7.6 Add SDK/SSE tests for event ordering, namespace/path, cursor reconnect, errors, cancellation and multiple interrupts.

## 8. Current graph and service integration

- [x] 8.1 Load the unchanged `runtime_service/langgraph.json` and validate all registered graphs.
- [x] 8.2 Run P0 graph E2E for `assistant`, `test_case_agent_v2`, `customer_support_handoffs_demo`, `deepagent_demo` and `personal_assistant_demo`.
- [x] 8.3 Verify RuntimeContext, RuntimeRequestMiddleware, custom capability routes and lifespan behavior through the new server.
- [x] 8.4 Define controlled fakes or real dependency requirements for graph tests and record unsupported external integrations.

## 9. Reliability and deployment acceptance

- [x] 9.1 Run local smoke with one API, one worker, host PostgreSQL and host Redis using the documented commands.
- [x] 9.2 Run reliability topology with two API processes and two workers against shared PostgreSQL/Redis.
- [x] 9.3 Verify API/worker/PG/Redis restart, rolling shutdown, queue backlog, SSE reconnect and replay.
- [x] 9.4 Removed Docker Compose parity scope after owner confirmed it is not needed.
- [x] 9.5 Removed Kubernetes reference scope after owner confirmed it is not needed.
- [x] 9.6 Emit structured logs and Prometheus/OTel-compatible metrics for run lifecycle, queue, lease/retry/reaper, SSE, HITL, PostgreSQL and Redis.

## 10. Release gate

- [x] 10.1 Run Python 3.11/3.12/3.13 CI and official Python/JavaScript SDK contract suites.
- [x] 10.2 Run security, tenant-isolation, migration, fault-recovery and P0 graph regression suites.
- [x] 10.3 Build lockstep `graphharbor` and `graphharbor-runtime` packages and verify no License Key is required.
- [ ] 10.4 Publish to TestPyPI only after all Core gates pass; validate isolated installation and rollback.
- [x] 10.5 Produce compatibility matrix, deployment runbook, incident recovery runbook and release notes before PyPI production release.
