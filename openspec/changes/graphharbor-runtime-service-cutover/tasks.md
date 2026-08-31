## 1. Cross-repository contract

- [ ] 1.1 Record the runtime-service source revision, GraphHarbor revision, Python/dependency lockfiles and supported graph exports.
- [ ] 1.2 Verify the existing runtime-service `langgraph.json` loads through `GraphRegistry.open()` and each supported async factory receives a per-Run `RunnableConfig`.
- [ ] 1.3 Define the platform gateway route flag, GraphHarbor base URL, Run route ownership and immediate rollback behavior without changing default traffic.

## 2. Production trust boundary

- [ ] 2.1 Generate a platform delegation JWT fixture with issuer, audience, expiry, jti, principal scope and model/tool policy claims without storing secrets in artifacts.
- [ ] 2.2 Verify API creation and Worker recovery reject missing, altered, expired, cross-scope and policy-incomplete RuntimeContext envelopes before model/tool execution.
- [ ] 2.3 Run two tenants and two projects through the real API and confirm resource reads, state, events, custom routes and MCP calls cannot cross scopes.

## 3. Durable execution and events

- [ ] 3.1 Run migration in an exclusive acceptance database and verify an idempotent second upgrade plus recorded schema head.
- [ ] 3.2 Start API and two Workers against the exclusive database and namespaced Redis; execute runtime-service deterministic and model-backed graphs.
- [ ] 3.3 Inject SIGTERM and SIGKILL at claim, checkpoint and finalize boundaries; verify lease reclaim, checkpoint continuation, retry limits and resource cleanup.
- [ ] 3.4 Inject a late Worker completion and duplicate queue message; verify one conditional terminal transition and one terminal event.
- [ ] 3.5 Verify v2/v3 SSE, thread events, `since`, `Last-Event-ID`, stale cursor behavior and HITL interrupt/resume across Worker replacement.

## 4. DeepAgent, MCP and capability isolation

- [ ] 4.1 Add a runtime-service DeepAgent acceptance graph that writes and reads a Thread-scoped marker through the configured Backend.
- [ ] 4.2 Verify the marker survives Worker replacement, while another thread/tenant cannot read it; reject absolute paths, `..`, symlink escape and unauthorized skill paths.
- [ ] 4.3 Verify parent and Subagent effective model, tools, skills, filesystem permissions and budget are the intersection of Service declaration and signed RuntimePolicy.
- [ ] 4.4 Verify bundled Skills are read-only, unneeded built-in `execute`/`task` capabilities are hidden or denied, and MCP resources close on success, error, cancel, timeout and shutdown.
- [ ] 4.5 Run inbound and outbound MCP acceptance with production JWT scope checks, name-collision rejection and no credential leakage.

## 5. Observability and failure isolation

- [ ] 5.1 Configure the approved Langfuse/OTLP endpoint and query real traces for Platform Run ID, Runtime Run ID, Thread ID, Agent version, model reference and policy version.
- [ ] 5.2 Prove trace metadata contains no credential, complete prompt/response, unrestricted tool argument or unapproved high-cardinality field.
- [ ] 5.3 Inject exporter 401, 429, 5xx, timeout, unreachable endpoint, bounded-queue full and flush timeout; verify Run finalize and original Agent error semantics are unchanged.
- [ ] 5.4 Verify JSON logs and Prometheus metrics expose success/error/timeout/cancel, tool errors, lease recovery, queue lag, checkpoint latency, replay gaps and exporter failures.

## 6. Release, migration and rollback

- [ ] 6.1 Build/install GraphHarbor and runtime-service in isolated Python 3.11/3.12/3.13 environments with locked dependencies and startup health checks.
- [ ] 6.2 Rehearse forward migration, backup/restore, code rollback on the forward-compatible schema and any approved downgrade in a disposable database.
- [ ] 6.3 Execute route rollout `0% -> 1% -> 10% -> 50% -> 100%` by tenant/project or percentage and capture latency, queue lag, PostgreSQL/Redis watermarks and error rates.
- [ ] 6.4 Disable GraphHarbor routing during an active Run and verify new Runs return to the prior path without losing Run, Event or Checkpoint facts.
- [ ] 6.5 Obtain owner approval only after every hard gate is `passed`, then update the readiness artifact to `ready_for_cutover` and document the maintenance window.
