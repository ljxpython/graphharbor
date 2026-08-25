# Verification

## Pre-apply review

已按生产冻结项执行：官方 Agent Server wire contract 为目标；生产不依赖
LangSmith License Key；Python 3.13 为主版本；生产凭据为 platform-api delegation
JWT；run 对外不增加 `cancelled`；基础设施重试上限为 3；本地 PostgreSQL/Redis
验收不以 Docker 为前置条件。

## Checks

| 检查 | 命令 | 结果 |
|---|---|---|
| OpenSpec installation | `openspec --version` + `openspec doctor --json` | `1.6.0`; healthy |
| OpenSpec | `openspec validate implement-production-agent-server --strict` | 通过 |
| Lockfile | `uv lock --check` | 通过 |
| Version baseline | `uv run python scripts/check_versions.py` | 通过 |
| Compatibility baseline | `uv run python scripts/check_compatibility_baseline.py --report` | 通过 |
| Static lint | `uv run ruff check ...` | 通过 |
| Type check | `uv run mypy ...` | 通过 |
| Contract tests | `uv run pytest libs/langhost/tests libs/langgraph-runtime-pg/tests/test_production_contract.py libs/langgraph-runtime-pg/tests/test_public_runtime.py -q` | 33 passed; full suite below |
| Runtime regression | `uv run pytest libs/langhost/tests libs/langgraph-runtime-pg/tests -q` | 76 passed, 14 skipped |
| Official Python SDK Core | `uv run pytest libs/langgraph-runtime-pg/tests/test_official_sdk_contract.py -q` | 3 passed; Core resources, v2 run SSE/replay and Protocol v2 multi-interrupt resume |
| Retry/reaper boundary | `uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py -q` | 25 passed; migration head, retry backoff, expired lease reclaim and HITL persistence covered |
| Protocol boundary | `uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py libs/langhost/tests/test_cli.py -q` | 37 passed; v2 capability, version validation, namespace depth, lifecycle projection and duplicate resume covered |
| Platform API v2 forwarding | `uv run python -m unittest discover -s tests -p 'test_runtime_gateway_sdk_adapters.py' -v` in `apps/platform-api` | 8 passed; `version="v2"` reaches official SDK stream call |
| Platform Web v2 forwarding | `pnpm test:run` in `apps/platform-web` | 120 passed; debug stream payload explicitly includes `version: 'v2'` |
| Queue/recovery boundary | `uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py -q` | 25 passed; rollback cleanup, FIFO job hints, cross-instance control fanout and Redis restart durability covered |
| Graceful shutdown ownership | `uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py libs/langgraph-runtime-pg/tests/test_queue.py -q` | 43 passed, 1 skipped; claimed run is released to `pending` with `shutdown_requeue` and thread returns idle |
| PostgreSQL persistence boundary | `uv run pytest libs/langgraph-runtime-pg/tests/test_persistence_contract.py -q` | 4 passed; isolated empty-schema migration, repeated upgrade, pool restart/reaper recovery, concurrent claim and rollback cleanup covered |
| Redis/worker/restart boundary | `uv run pytest libs/langgraph-runtime-pg/tests/test_production_contract.py -q` | 30 passed; namespaced FIFO queue, cross-instance cancel fanout, one durable terminal cancel event, API/Redis restart, worker kill/reaper recovery and multi-worker claim covered |
| Production package metadata | `uv build --package graphharbor-runtime` + wheel `METADATA` inspection | 无生产 `langgraph-api` 依赖 |
| Config/graph registry | `uv run pytest libs/langgraph-runtime-pg/tests/test_public_runtime.py -q` | 4 passed; standard config and project-root-relative graph paths load |
| Runtime executor identity/checkpointer regression | `uv run pytest libs/langgraph-runtime-pg/tests/test_public_runtime.py -q` | 5 passed; worker config carries `thread_id` plus public `Runtime(ServerInfo)` identity |
| Runtime-service integration | `apps/runtime-service/.venv/bin/pytest -q runtime_service/tests/harness/test_graphharbor_integration.py` | 3 passed; unchanged config loads all 9 graphs, P0 context schemas, `platform_auth`, custom routes and shared Principal |
| Runtime-service package import | `apps/runtime-service/.venv/bin/python` registry smoke | 9/9 graphs import in the real Python 3.13 environment |
| P0 graph E2E with live model | `GRAPHHARBOR_P0_E2E=1 ... uv run pytest libs/langgraph-runtime-pg/tests/test_runtime_service_p0_e2e.py -q -o timeout=900` against a real Uvicorn API/worker, host PostgreSQL/Redis, platform delegation JWT and DeepSeek credentials injected from `open-swe/.env` | 5 passed in 21.11s; `assistant`, `test_case_agent_v2`, `customer_support_handoffs_demo`, `deepagent_demo`, `personal_assistant_demo` completed official Python SDK v2 streams and persisted runs |
| Official JavaScript SDK Core | `node scripts/test_js_sdk.mjs` against a real Uvicorn server | Passed with `@langchain/langgraph-sdk` 1.9.28; assistants, threads, runs, batch/cancel and cron |
| Single-instance deployment | documented `migrate` + one API + one worker with host PostgreSQL/Redis | Existing controlled smoke passed; after the production executor fix, the live P0 request reaches the configured model provider |
| Multi-instance deployment | two API + two workers sharing host PostgreSQL/Redis | Four concurrent runs completed successfully; no duplicate claim observed |
| API restart durability | stop API 1, query through API 2, restart API 1 | Existing run remained `success`; API 1 rejoined cleanly |
| Structured runtime logs | Start the real `runtime-service` Uvicorn/API + worker harness with host PostgreSQL/Redis | Lifecycle startup emitted JSON `PG pool started` and `GraphHarbor production runtime ready` records, each with `event`, `level` and UTC `timestamp` |
| Prometheus-compatible metrics | `GET /metrics` during the live harness; inspect the P0 E2E snapshot | Exported PostgreSQL pool, Redis connectivity and queue gauges; P0 E2E recorded `runs_created=5`, `runs_claimed=5`, `runs_completed=5`, `lease_claims=5`, `queue_enqueued=5`, `queue_depth=0`, plus v2 SSE connection/event counters |
| Three-version regression | Isolated Python 3.11/3.12/3.13 environments with independent PostgreSQL/Redis databases | Each environment: `53 passed, 2 warnings`; official Python and JavaScript SDK contracts included |
| Final package build | `uv build --package graphharbor-runtime --out-dir /tmp/graphharbor-release-final` + `uv build --package graphharbor --out-dir /tmp/graphharbor-release-final` | Both `0.13.0.post1` source distributions and wheels built successfully |
| Final wheel isolation | `uv run --isolated --no-project --with <runtime-wheel> --with <cli-wheel>` import and CLI checks | Both packages import, CLI reports `0.13.0.post1`, no production `langgraph-api` or License Key requirement |
| Release documentation | `docs/compatibility-matrix.md`, `docs/production-runbook.md`, `docs/incident-recovery.md`, `docs/release-notes-0.13.0.post1.md` | Present and consistent with host PostgreSQL/Redis deployment |

## Implemented boundary

- production CLI 不再导入 `langgraph_api.cli`，并提供独立 `migrate`、`serve`；worker 默认
  fail-closed，旧 worker 只有显式 `--compatibility-spike` 才可启动。
- 自有 ASGI app 提供 `/ok`、`/live`、`/ready`、`/info`、`/openapi.json`，并组合 custom app lifespan。
- `/ready` 同时检查 PostgreSQL schema contract；仅能连上数据库但未执行显式 migration 时保持未就绪。
- 增量 migration `002_production_runtime` 增加租户/项目 scope、run reason、retry、幂等键、lease、heartbeat、事件序号、事件表和 runtime schema version。
- `Principal`、JWKS TTL cache、issuer/audience/signature/expiry 校验和生产 fail-closed middleware 已实现。
- 生产 JWT 算法通过 `GRAPHHARBOR_JWT_ALGORITHMS` 显式配置，默认只允许 RS256；custom routes
  复用同一 Principal，租户越权返回 404，客户端 scope 覆盖返回 403。
- `RunRepository` 提供幂等创建、`FOR UPDATE SKIP LOCKED` claim、lease renew、terminal transition 和 expired requeue。
- v3 graph stream 正确等待异步 `output()`/`interrupts()` 并返回 `GraphOutput`，同时通过共享
  投影函数规范化 `messages`、`values`、`updates`、`custom`、`tools`、`lifecycle`、`input`
  和子图 namespace 的 `method`、`params.namespace`、`params.timestamp`、`params.data`、`seq`。
- `/info` 将 assistants、threads、runs、cron、v2 SSE、Protocol v2 和 v3 typed projections
  标为 available；Extended store 仍明确标为 unavailable，不伪造成功。
- worker heartbeat 监听 stop event，优雅停机将运行中的 lease 原子释放为 `pending`，reason 为
  `shutdown_requeue`，并恢复 thread idle。
- `runs` 增加 `next_attempt_at` 和 005 migration；基础设施失败按有界指数退避，reaper 独立回收
  过期 lease 并恢复 thread 状态。
- Graph registry 支持 nested `langgraph.json` 布局，并将配置项目根加入 import path，保持 graph
  与 custom app 的包内绝对导入可用。
- `langgraph.json` 的 `auth.path` 现在由 GraphHarbor 加载并用于认证；`platform_auth` 用户映射为
  同一 `Principal`，Core/custom routes 共享租户与项目隔离。
- CLI 会按配置项目根解析并加载 `env` 文件，API 与 worker 使用相同配置入口。
- 官方 Python SDK 已实测 Core 资源、run SSE `version="v2"`、`Content-Location`、子图 namespace、
  terminal end、cursor replay，以及 Protocol v2 多 interrupt、歧义拒绝、显式 resume 和幂等 resume。

## Uncovered boundaries

以下仍不能作为生产完成证据：v3 真实 graph typed projection E2E、Protocol v2 长连接/重连在
真实网络传输下的组合。TestPyPI 上传和隔离安装仍待具备发布凭据后执行。

本轮真实 P0 harness 已在可用 DeepSeek provider 下验证 API/worker/PG/Redis/SDK/stream 连接、
platform delegation JWT、模型调用和 run 持久化；五个 graph 均通过官方 Python SDK v2 stream。
MCP/skills 等外部集成的专门路径仍需分别满足其依赖，不能由本轮基础 P0 输入替代。

## Docs / runbook impact

本轮更新 `.env.example`、`docs/production-runbook.md` 与 `docs/runtime-service-production-acceptance.md`；生产 migration 必须在 API
和 worker 前单独运行，PostgreSQL 与 Redis 使用独立主机服务。

本轮还修复了 runtime 模块重载场景下旧适配器持有 stale checkpointer 工厂的问题、worker
缺失 `thread_id`/Agent Server `Runtime` 身份，以及 thread-scoped stream 路由未传递
`thread_id` 的问题；全套 `langhost`/`langgraph-runtime-pg` 回归为 77 passed、15 skipped。单实例、多实例和 API
重启证据已补入上表，但仍不替代 PG/Redis 重启与真实 P0 graph 外部依赖验收。

当前项目 `apps/runtime-service/runtime_service/langgraph.json` 的路径解析、9 个 graph 导入、
`platform_auth` 和 custom routes 已在其真实 Python 3.13 环境验证。P0 graph 的真实执行仍需要
模型 provider、工具绑定能力、LightRAG/Tavily MCP 和 skills 目录，详见验收边界文档；不得用
GraphHarbor 自己的测试依赖替代这些外部集成。
