# GraphHarbor 生产实施冻结项讨论

本文把进入代码实施前必须冻结的六项内容写成可执行的决策草案。它不是新的架构文档，而是对 [`production-decisions.md`](production-decisions.md)、[`compatibility-profile.md`](compatibility-profile.md) 和 [`production-implementation-plan.md`](production-implementation-plan.md) 的 owner review 入口。

六项 owner 决策已于 2026-08-25 完成确认；后续实现以本文“已确认”内容为约束。

状态含义：

- **事实**：已由当前仓库、当前项目调用方或官方文档确认。
- **已确认**：owner 已确认，可以作为实现约束。
- **待确认**：仍需 owner 决定；当前六项已不存在待确认项。

## 1. Core Compatibility Profile

### 事实

当前项目不是只有聊天流。`platform-api` 的 LangGraph SDK adapter 已经暴露并调用：

```text
assistants.get/create/update/delete
threads.get/create/search/count/prune/update/delete/copy
threads.get_state/update_state/get_history
runs.create/create_global/create_batch
runs.get/list/delete/wait/stream/join/join_stream
runs.cancel/cancel_many
crons.create/search/count/update/delete
crons.create_for_thread
```

`platform-web`/`runtime-web` 当前使用远程 `client.runs.stream(...)`。`runtime-service` 还要求保留 `langgraph.json`、全部 graph、custom routes、custom auth、`RuntimeContext` 和 middleware 契约。

一个必须记录的现状缺口：当前 `debug.service.ts` 没有显式传 `version`，而 `platform-api` 的 `_STREAM_FIELDS` 也没有把 `version` 转发到 SDK。因此“当前已有远程 v2”不能当作已验收事实；S4 必须显式加入版本字段、确认目标 SDK 的参数位置，并用真实 SSE 断言 v2。兼容目标不变，现状只标记为 **待升级/待验证**。

### 已确认冻结值

GraphHarbor 第一版对外 Core 不是“当前页面调用的最小子集”，而是下面九个资源面：

| Core 面 | 必须包含 |
|---|---|
| 配置/发现 | `langgraph.json`、graphs、dependencies、env、auth、`http.app`、`/ok`、`/info`、`/openapi.json` |
| assistants | 当前 adapter 使用的 get/create/update/delete，以及 SDK 所需的发现字段 |
| threads | CRUD、search、count、copy、prune、state、history、update_state |
| runs | create/get/list/delete/wait/stream/join/join_stream、batch create、single cancel、bulk cancel |
| crons | 当前 adapter 暴露的全局和 thread cron CRUD/search/count |
| remote v2 | `client.runs.stream(..., version="v2")`、多 stream mode、`stream_subgraphs`、resumable/replay |
| remote events | 官方线程级事件流、commands、v3 event projections、seq/cursor、子图 namespace/lifecycle |
| HITL | `interrupt`、`Command(resume=...)`、多 interrupt、断线/重启后恢复 |
| 生产运行 | PostgreSQL、Redis、worker lease/heartbeat/reaper、跨实例 cancel、graceful drain/requeue |

`/runs/batch`、`/runs/cancel`、`/threads/count`、`/threads/{thread_id}/copy` 不再列入 Extended；它们已经是当前项目兼容面的一部分。cron 同理，因为 `platform-api` 已提供对应路由，即使前端暂时未调用，也不能在 GraphHarbor 替换后变成 501。

### Extended / Unavailable

第一版仍不把以下能力作为 Core：`store`、webhook、MCP、A2A、Generative UI、高级管理 API、多区域 HA。它们必须在 `/info`/capabilities 和文档中明确为 Extended 或 Unavailable，不能返回伪成功。

### 验收门槛

每个 Core 面都必须同时通过：

1. 官方 Python SDK。
2. 官方 JavaScript SDK。
3. REST/OpenAPI 请求和错误响应。
4. 至少一个 multi-worker 和一次 PostgreSQL/Redis 重启场景。
5. 租户、认证、跨租户 404 和重复请求测试。

### 确认结果

`store` 暂时保持 Extended，不纳入第一版 Core。当前 `platform-api` 没有直接调用 LangGraph store API；graph 内部如有 store 需求，先通过独立测试标记，不伪装成已兼容。

## 2. 目标 LangGraph / SDK 版本

### 事实

截至 2026-08-25，从 PyPI 查询到的最新发布版本是：

| 包 | 最新版本 | 用途 |
|---|---:|---|
| `langgraph` | `1.2.11` | graph 执行和本地 v2/v3 API |
| `langgraph-sdk` | `0.4.3` | 官方 Python SDK wire/client 基线 |
| `langgraph-cli` | `0.4.31` | 配置校验、开发/构建辅助 |
| `langgraph-api` | `0.13.0` | 仅内部 compatibility spike，不作为最终生产依赖 |
| `langgraph-checkpoint` | `4.2.0` | checkpoint 合约 |
| `langgraph-checkpoint-postgres` | `3.1.2` | PostgreSQL checkpoint |
| `langgraph-runtime-inmem` | `0.33.0` | 仅用于对照实验/兼容测试 |

官方文档当前的 Agent Server run 状态、Protocol v2、`stream_events(version="v3")` 和 SDK 语义以目标版本的真实 OpenAPI/SDK 为准，不以旧的 `langgraph-api==0.11.1` 推断。

### 已确认冻结值

```text
生产主验证：Python 3.13
CI 兼容矩阵：Python 3.11 / 3.12 / 3.13
LangGraph 主线：1.2.11
SDK 主线：0.4.3
CLI：0.4.31
checkpoint：4.2.0
checkpoint-postgres：3.1.2
```

`langgraph-api==0.13.0` 只允许出现在 S1 compatibility spike 环境。GraphHarbor 最终 Agent Server 必须在没有 LangSmith 托管和 `LANGGRAPH_CLOUD_LICENSE_KEY` 的环境中启动，因此不能把该包写成最终部署的硬依赖。

### 版本规则

1. 发布包和 lockfile 使用精确版本，不使用无限制的 `>=` 作为生产基线。
2. 每个 GraphHarbor 版本发布一张兼容矩阵，绑定 Python、LangGraph、SDK、PostgreSQL 和 Redis。
3. 官方包有新版本时先跑 S1/S2/S4 回归，再决定是否升级；不能仅自动更新 lockfile 就发布。
4. SDK/OpenAPI 发生 breaking change 时，GraphHarbor 版本必须升 minor 或 major，并保留上一条兼容矩阵。

### 确认结果

owner 已确认 Python 3.13 为生产主版本，CI 同时验证 3.11/3.12。`langgraph-api==0.13.0` 仍只允许作为 S1 内部实验依赖，不改变最终无 License Key 目标。

## 3. 当前项目真实调用清单

### 调用分层

| 调用方 | 真实调用 | 兼容级别 | 证据 |
|---|---|---|---|
| `platform-api` runtime gateway | threads 全量 CRUD/search/count/copy/prune/state/history；runs create/get/list/delete/wait/stream/join/cancel/batch/bulk cancel；cron 全量操作 | Core | `apps/platform-api/app/adapters/langgraph/*_sdk_adapter.py`、`runtime_gateway_upstream.py` |
| `platform-api` assistants | assistant get/create/update/delete；平台侧 list 由本地 service 负责 | Core | `apps/platform-api/app/adapters/langgraph/assistants_client.py`、`modules/assistants` |
| `platform-web` / `runtime-web` | 远程 `client.runs.stream`，传入 stream mode、subgraphs、resumable 等字段 | Core | `apps/platform-web/src/services/runtime-gateway/debug.service.ts` 及其调用链 |
| `runtime-service` | `langgraph.json` graph discovery；custom `/internal/capabilities/models`、`/internal/capabilities/tools` | Core | `runtime_service/langgraph.json`、`runtime_service/custom_routes` |
| graph 代码 | `RuntimeContext`、`RuntimeRequestMiddleware`、interrupt/HITL、supervisor/deep-agent 子图 | Core | `runtime_service/agents`、`runtime_service/services` |

### 清单冻结格式

正式基线文件 `docs/compatibility-call-surface.md` 的每行必须包含：

```text
调用方
SDK 方法
HTTP endpoint
请求字段
响应字段
认证 Principal
租户/项目过滤
Core/Extended/Unavailable
测试文件
```

### 确认结果

真实调用清单已确认采用 adapter、presentation route 和现有前端调用的并集。不能只按当前页面是否点击过来删接口。cron 当前虽可能没有前端主流程，但已有平台路由，因此属于 Core。

## 4. Principal / Auth Contract

### 事实

当前 `platform_auth` 已校验 delegation JWT 的 `sub`、`tenant_id`、`project_id`、`role`、`jti`、permissions、issuer、audience、算法和有效期，并区分 `runtime_delegation` 与 `runtime_management`。`provider.py` 另有 demo token/API key 和 Supabase OAuth 路径。

### 已确认冻结值

生产集成模式统一采用 platform-api 签发的短时 delegation JWT；runtime 不向每个请求回调平台 API，而是使用本地 JWKS/公钥校验。认证成功后只生成一个规范 Principal：

```text
identity       <- JWT.sub
tenant_id      <- JWT.tenant_id
project_id     <- JWT.project_id
roles          <- JWT.role / roles
scopes         <- JWT.permissions / scopes
credential_type<- runtime_delegation | runtime_management | user
delegation_id  <- JWT.jti
issuer         <- JWT.iss
audience       <- JWT.aud
issued_at      <- JWT.iat
expires_at     <- JWT.exp
```

规则：

1. Agent Server API 和 custom routes 复用同一个 Principal，不再各自解析 token。
2. 客户端提交的 `tenant_id`、`project_id`、`user_id` 不能覆盖 Principal。
3. `runtime_management` 只访问管理面，不读取用户 thread/run 数据。
4. 资源查询必须带 tenant/project filter；跨租户资源统一返回 404。
5. demo token、固定 API key 只允许测试 profile，生产启动时拒绝启用。
6. 独立部署可以增加 OIDC/JWKS authenticator，但必须映射到同一 Principal 和授权规则，不能创建第二套资源权限语义。
7. custom routes 的 401/403/404 语义必须与官方资源路由一致。

### 确认结果

生产集成模式的唯一签发凭据是 `platform-api` delegation JWT。runtime 不把公网 Supabase/OIDC token 作为生产集成模式的默认入口；`custom_auth`/`oauth_auth` 仅保留为兼容/开发或显式 standalone profile。

## 5. Run / HITL / Cancel 状态机

### 官方外部状态

对外只使用官方 run 状态：

```text
pending -> running -> success
                   -> error
                   -> timeout
                   -> interrupted
```

官方 cancel 默认是异步请求；`wait=true` 才等待取消完成。取消完成后的官方 run 状态是 `interrupted`，不是新增 `cancelled`。HITL 的 `interrupt(...)` 也使用 `interrupted`，两者通过 interrupt payload、事件和内部 reason 区分。

### 内部 reason

数据库可以保存以下内部原因，但不把它们冒充官方 status：

```text
hitl_interrupt
cancel_requested
shutdown_requeue
multitask_interrupt
timeout
retry_exhausted
```

### 已确认规则

1. `interrupt(payload)` 写入 checkpoint，run 进入 `interrupted`，响应包含可恢复 interrupt。
2. `Command(resume=...)` 只恢复同一 thread/checkpoint；重复 resume 必须幂等，不能重复执行已完成工具副作用。
3. `runs.cancel` 幂等；晚到 cancel 不能覆盖 `success/error/timeout`。
4. `action=interrupt` 只停止执行；`action=rollback` 同时删除 run 和关联 checkpoint，语义按官方接口。
5. `cancel_many` 使用官方 `/runs/cancel` 的 `thread_id/run_ids/status/action` 过滤语义，不另造批量协议。
6. 同一 thread 默认使用官方 `multitask_strategy="enqueue"`；同时支持 `reject`、`interrupt`、`rollback`。需要严格串行的业务显式传 `reject` 或 `enqueue`，不能用私有锁改变 SDK 语义。
7. 基础设施失败才自动重试；业务拒绝、HITL 等待、用户取消不重试。最多自动重试 3 次，指数退避 1/2/4 秒并设置上限；最终失败为 `error`。
8. worker kill 通过 PostgreSQL lease/reaper 恢复；graceful shutdown 对未执行任务 requeue，不写成业务 error。

### 验收顺序

```text
普通 run -> success
业务异常 -> error
超时 -> timeout
interrupt -> interrupted + resumable interrupt
resume -> 原 thread 继续且不重复副作用
cancel(wait=false) -> 异步请求
cancel(wait=true) -> join 后 interrupted
rollback -> run/checkpoint 删除
worker kill -> lease reclaim/retry
```

### 确认结果

owner 已确认：不增加独立 `cancelled` run status；默认遵循 `multitask_strategy="enqueue"`；基础设施错误最多自动重试 3 次。

## 6. 首阶段部署和验收环境

### 已确认冻结值

第一阶段验收必须支持本地直接部署：

```text
1 个 Agent Server API
1 个 worker
1 个 PostgreSQL（pgvector/pg16 基线）
1 个 Redis（固定版本，禁止 latest）
1 个独立 migration 命令
```

本机已安装的 PostgreSQL/Redis 可以直接作为首阶段验收依赖，必须使用独立数据库名和 Redis key prefix，不能污染已有 `langgraph`、`runtime_service` 等数据库。

第一阶段验收分三档：

| 档位 | 拓扑 | 目的 |
|---|---|---|
| smoke | 1 API + 1 worker + PG + Redis | SDK、custom routes、P0 graph、HITL 基本链路 |
| reliability | 2 API + 2 worker + 共享 PG/Redis | SSE 跨实例、lease、cancel、replay、扩容 |
| fault | reliability 拓扑 + kill/restart | worker/API/PG/Redis 重启、断线、requeue |

至少验收：

1. Python/JavaScript SDK 不改调用代码。
2. `client.runs.stream(..., version="v2")` 和线程事件流。
3. P0 graph：`assistant`、`test_case_agent_v2`、`customer_support_handoffs_demo`、`deepagent_demo`、`personal_assistant_demo`。
4. custom routes/auth/lifespan。
5. 子图 lifecycle、HITL interrupt/resume、断线 replay。
6. worker kill、API rolling restart、PG/Redis restart。

第一阶段不把特定云厂商、性能 SLO 和旧数据迁移作为本地门禁。但生产发布前必须补齐目标服务器资源、并发 run、SSE 连接数、P95 首事件/完成延迟、数据保留期和回滚方式。

### 确认结果

owner 已确认项目只支持本地进程部署，不提供 Docker Compose 或 Kubernetes 资源。旧数据迁移、目标 SLO 和可观测性系统仍属于后续运行参数，不阻塞本六项冻结。

## 冻结结论

六项冻结结果：

| 项 | 推荐值 | owner 必须确认的最小问题 |
|---|---|---|
| Core Profile | 当前 adapter 并集 + v2/v3/HITL/子图/生产可靠性；store Extended | 已确认 |
| 版本 | `langgraph 1.2.11`、SDK `0.4.3`、Python 3.13 主线，CI 验证 3.11/3.12 | 已确认 |
| 真实调用 | adapter + route + 前端 + graph 的并集 | 已确认 |
| Principal | platform-api delegation JWT，custom routes 共用 Principal | 已确认 |
| 状态机 | 官方 status；cancel/HITL 均为 interrupted；默认 enqueue；基础设施重试 3 次 | 已确认 |
| 首阶段环境 | 本地 API/worker + PostgreSQL/Redis 必选；无容器编排资源 | 已确认 |

六项现在已经可以作为代码实施约束。后续实现仍必须先通过对应切片的 OpenSpec review 和验收门禁，不能因为冻结完成就跳过协议/SDK/E2E 验证。
