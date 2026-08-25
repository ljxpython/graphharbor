# GraphHarbor 生产化架构讨论与决策记录

本文记录 GraphHarbor 与当前 `runtime-service` 生产化讨论中的事实、决策、推荐方案和开放问题。

分层兼容范围和垂直切片定义见 [`compatibility-profile.md`](compatibility-profile.md)。六项正式冻结讨论见 [`production-freeze-decisions.md`](production-freeze-decisions.md)。

规则：六项冻结内容以 [`production-freeze-decisions.md`](production-freeze-decisions.md) 为准；其中已确认项可以直接进入实现。其他“推荐”内容仍需在对应切片前完成局部 review。

## 1. 已确认目标

### D1：生产目标

已确认采用：

> 生产目标是官方 LangGraph Agent Server 的协议和运行语义，并提供接近 `langgraph dev` 的使用体验。

`langgraph dev` 只作为开发体验参考，不作为生产运行时。目标能力包括：

~~~text
官方 Agent Protocol / SDK 兼容
PostgreSQL 持久化
Redis 队列、Pub/Sub 与跨实例流
当前项目全部 custom routes
当前项目 custom auth
HITL interrupt / resume
子图生命周期事件
v2 stream 与 v3 event stream
断线续传
多 worker、扩容、取消和故障恢复
~~~

### D2：无 LangSmith 托管和无 License Key

已确认：

- 不依赖 LangSmith 托管服务。
- 不依赖 `LANGGRAPH_CLOUD_LICENSE_KEY`。
- 兼容官方 Agent Protocol 和 LangGraph SDK。

任何需要 License Key 才能启动的组件不能成为最终生产必需依赖。

### D3：依赖版本

已确认使用最新稳定版本的 LangGraph 依赖，并同步升级 ai-agent-platform 内对应依赖。

每个 GraphHarbor 版本必须声明并验证一组兼容矩阵：

~~~text
graphharbor
graphharbor-runtime
langgraph-api / Agent Server adapter
langgraph-sdk
langgraph
langgraph-cli
Python
PostgreSQL
Redis
~~~

### D4：custom routes

当前项目的 custom routes 全部保留，不能为了适配 runtime 删除或静默改变路径、权限和响应语义。

每个路由必须有：路径、请求/响应模型、认证方式、租户边界、调用服务和端到端用例。

### D5：远程流迁移顺序

已确认：

1. 第一阶段保留当前远程 `client.runs.stream(..., version="v2")`。
2. 第二阶段增加事件流能力和服务端协议验证。
3. 第三阶段前端按事件投影、子图生命周期和 HITL 功能逐步迁移。

### D6：v2/v3 能力范围

已确认需要完整支持目标版本提供的：

- `updates`
- `messages`
- `values`
- `custom`
- `debug`
- `events`
- 多模式流
- 子图 namespace / path / lifecycle
- HITL interrupt / resume
- 错误事件
- 断线续传

“支持”必须由真实 SDK/HTTP 测试断言，不能只看 HTTP 200。

### D7：子图是 P0

子图生命周期是 P0。第一期必须验证子图开始、事件归属、结束、失败以及父子图路径关系；前端展示可以后置，服务端事件不能后置。

## 2. Aegra、LangHost、GraphHarbor 边界

### 2.1 Aegra 的边界

Aegra 是完整的独立开源 Agent Server：

~~~text
FastAPI HTTP/API
Agent Protocol 路由
PostgreSQL 数据与 checkpoint
Redis job queue / Pub/Sub / replay
worker、lease、reaper、跨实例 cancel
auth / authorization
custom routes
HITL
Agent Protocol v2 thread-scoped SSE
~~~

Aegra 的配置是 `aegra.json`，它自己拥有 HTTP 协议实现，不依赖官方 `langgraph-api` 的运行时许可。

值得借鉴的实现模式：

1. `merge_lifespans`：core lifespan 包裹 user app lifespan，启动先初始化平台资源，关闭逆序清理。
2. 线程级 v2 SSE：`POST /threads/{id}/stream/events` + `POST /threads/{id}/commands`。
3. Redis 队列 + PostgreSQL lease：API 与 worker 解耦，worker 崩溃后 reaper reclaim。
4. Redis Pub/Sub + replay buffer：支持跨实例实时流和断线恢复。
5. 认证与授权分离：认证产生 Principal，授权处理 resource/action/filter。
6. 跨实例 cancel：数据库状态、Redis 控制消息和终止事件协作。
7. v2 capability probe 和 kill switch。

Aegra 不应直接作为当前项目依赖：它有另一套 API、runtime、配置和 auth 边界，直接切换会迫使当前项目同时迁移 `langgraph.json`、custom routes、auth 和客户端行为。

参考：

- [Aegra README](https://github.com/aegra/aegra)
- [Aegra worker architecture](https://docs.aegra.dev/guides/worker-architecture)
- [Aegra streaming](https://docs.aegra.dev/guides/streaming)
- [Aegra authentication](https://docs.aegra.dev/guides/authentication)
- [Aegra HITL](https://docs.aegra.dev/guides/human-in-the-loop)

### 2.2 LangHost 的边界

LangHost 的设计是：

~~~text
官方 langgraph-api
        +
自定义 langgraph-runtime-pg
        +
CLI / migration / packaging
~~~

优点：

- 保留官方 Agent Server API、Studio、SDK 和生态协议。
- 当前 graph 理论上不需要重写。
- runtime 负责 PostgreSQL、Redis、队列和持久化。

风险：

- HTTP/API 仍由官方 `langgraph-api` 控制。
- 官方 server package 的许可、启动 gate 和内部 API 变化不能由 LangHost 自己决定。
- custom FastAPI app 的 lifespan 组合不是自动保证的。
- runtime 兼容不等于最新 Agent Server 完整兼容。

参考：[LangHost README](https://github.com/langhost/langhost)

### 2.3 GraphHarbor 推荐边界

在“无 LangSmith 托管、无 License Key、兼容官方 Agent Protocol/SDK”的硬约束下，GraphHarbor 最终不应只停留在 LangHost 的 runtime fork。

推荐分层：

~~~text
GraphHarbor Core Runtime
  PostgreSQL / Redis / queue / checkpoint / replay / lease

GraphHarbor Agent Protocol Adapter
  threads / assistants / runs / stream / commands / events / HITL

GraphHarbor Application Adapter
  langgraph.json / graphs / auth / custom routes / RuntimeContext

Official SDK Contract
  langgraph-sdk-compatible wire behavior
~~~

推荐采用两阶段，但两阶段都不允许用户修改业务 graph 或客户端调用：

#### 阶段 A：内部 compatibility spike

- 使用当前 GraphHarbor runtime 验证最新 LangGraph 依赖。
- 可以暂时使用 server adapter 做兼容性实验，但只作为内部验证工具。
- 不作为对外产品模式，不要求用户迁移，也不作为“生产兼容”结论。
- 实验结果只用于确定哪些 Agent Protocol 能力需要 GraphHarbor 自己实现。

#### 阶段 B：无 License Key 的完整 drop-in Agent Server

- 以公开 Agent Protocol 和 SDK 行为作为契约。
- 借鉴 Aegra 的 HTTP、worker、v2 event streaming、auth、lifespan 设计。
- 复用 GraphHarbor 的 PostgreSQL/Redis runtime。
- 不复制官方闭源实现，不依赖官方 server 私有模块。
- 保持标准 `langgraph.json`，应用 graph 源码无需修改。
- 保持官方 `langgraph-sdk` 的 Python/JavaScript 调用方式无需修改。
- 保持官方 REST 路径、请求/响应模型、SSE 编码和事件语义。
- 保持 Studio、Agent Chat UI、CopilotKit 等标准客户端的连接方式。
- 保持当前项目 custom routes、custom auth 和 `RuntimeContext` 契约。

用户侧允许的变化仅限于部署替换：安装 GraphHarbor、选择 GraphHarbor 启动命令、配置 PostgreSQL/Redis 和 API 地址；不允许增加业务适配层、客户端 wrapper 或协议转换层。

### 2.5 Drop-in 兼容的硬性定义

GraphHarbor 在未满足下面条件前，不得宣称“兼容官方 LangGraph Agent Server”：

| 兼容面 | 硬性要求 |
|---|---|
| 应用配置 | 原有 `langgraph.json` 可直接加载；graphs、dependencies、env、auth、http app 等目标字段行为一致 |
| graph 代码 | 现有 graph 不改源码即可加载和执行 |
| Python SDK | 官方 `langgraph-sdk` 的现有调用不改代码即可工作 |
| JavaScript SDK | 官方 `@langchain/langgraph-sdk` 的现有调用不改代码即可工作 |
| REST API | 目标版本的 threads、assistants、runs、state、history、store、cancel、batch 等 endpoint 和错误语义有契约测试 |
| 流式 API | v1/v2 run stream、v2 thread event stream、v3 event projection、heartbeat、cursor、replay 有真实 SSE 测试 |
| HITL | 官方 `interrupt`、`Command(resume=...)`、approve/reject/edit/respond 和多次 interrupt 可恢复 |
| 子图 | `subgraphs=true`、namespace/path、lifecycle、messages/values/updates 可被官方客户端消费 |
| custom routes | 原路径、认证、响应和 lifespan 全部保留 |
| Studio/UI | 官方 Studio、Agent Chat UI 至少完成 smoke；不引入 GraphHarbor 专用前端适配 |
| CLI/config | 提供等价的 serve/dev/build/migrate 能力；命令名可以不同，但配置和运行结果不能不同 |
| 许可/依赖 | 运行时不需要 LangSmith 托管、API Key 或 License Key |

“官方方式一样”指应用和客户端的兼容性，而不是复刻官方内部实现。部署命令可以是 `graphharbor serve`，但应用代码、配置、SDK 和协议不能要求改写。

### 2.4 需要 owner 拍板的边界

| 选项 | 含义 | 推荐 |
|---|---|---:|
| 仅 runtime fork | 继续依赖官方 `langgraph-api` | 不满足无 License Key 最终目标 |
| 部分 adapter | 只实现当前项目用到的少数接口 | **禁止对外发布** |
| 立即完整重写 | 第一版直接做完整开源 Agent Server | 风险大、验证周期长 |
| 分阶段 drop-in | 内部 spike 后实现完整自有兼容层 | **推荐** |

## 3. 当前项目对齐方案

### D8：custom auth 最佳实践（推荐）

采用四层模型：

~~~text
Authentication：验证 token、issuer、audience、签名、有效期
Principal：subject、tenant_id、project_id、roles、scopes、credential_type
Authorization：resource + action + tenant/project filter
RuntimeContext：只承载已验证的可信身份和项目字段
~~~

规则：

1. Agent Server `Auth` 是 threads/runs/assistants 的统一信任入口。
2. custom routes 使用同一套 Principal 和授权依赖，不自行解析第二种 token。
3. 平台内部 delegation credential 与终端用户 credential 分开。
4. 客户端提交的 `project_id`、`tenant_id`、`user_id` 不能覆盖认证结果。
5. graph 不从消息、state 或普通 configurable 字段推断身份。
6. 所有资源查询带租户过滤；跨租户资源统一返回 404。
7. 新增 custom route 必须有 auth contract 和测试。

现有 `runtime_service/auth/platform.py` 和 `auth/provider.py` 应沿此方向收敛，不增加第三套身份来源。

### D9：lifespan 责任和顺序（推荐）

~~~text
外层：Agent Server
  HTTP、配置、server 级资源

中层：GraphHarbor runtime
  PostgreSQL pool、Redis、queue、worker、replay、metrics

内层：runtime-service custom
  业务 client、MCP、模型 catalog、文件/知识服务
~~~

启动：

~~~text
配置校验
→ PostgreSQL/Redis readiness
→ runtime 连接池和 worker
→ custom 业务资源
→ readiness=true
~~~

关闭：

~~~text
readiness=false
→ 停止接收新 run
→ worker drain / requeue
→ custom 资源关闭
→ runtime pool/Redis/worker 关闭
→ server 关闭
~~~

不混用 `on_startup`/`on_shutdown` 与 lifespan；生产 migration 使用独立命令。

### D10：生产支持 graph 列表（推荐）

#### P0：第一期必须支持

1. `assistant`：默认入口、middleware、工具和 HITL 基线。
2. `test_case_agent_v2`：核心测试用例业务、MCP、项目范围和多模态。
3. `customer_support_handoffs_demo`：handoff 和状态流转。
4. `deepagent_demo`：子代理、skills、文件后端、子图生命周期。
5. `personal_assistant_demo`：supervisor、子代理协同和 resume。

#### P1：兼容性通过后纳入

1. `research_demo`：外部 Tavily/MCP 依赖需要独立故障策略。
2. `skills_sql_assistant_demo`：技能注入和工具筛选需要安全验证。
3. `sql_agent`：示例数据库和工具安全边界需要单独定义。

P1 不得破坏 P0，但不阻塞第一版生产 runtime 发布。

## 4. 协议、事件和 HITL

### D11：协议分层

必须分别验证：

~~~python
graph.stream(..., version="v2")
graph.stream_events(..., version="v3")
client.runs.stream(..., version="v2")
~~~

本地 graph API 成功不等于远程 SDK 成功；远程验证必须经过真实 HTTP/SSE 服务。

### D12：HITL 官方语义

官方 LangGraph 的定义：

1. graph 在决策点调用 `interrupt(payload)`。
2. 执行暂停，持久化层保存可恢复状态。
3. 稳定的 `thread_id` 是暂停和恢复的前提。
4. 服务端返回 interrupt payload 和可恢复信息。
5. 人工决定后，以 `Command(resume=...)` 继续同一个 thread。
6. resume 值回到 `interrupt()` 调用处，graph 从暂停点继续。

常见决定：

~~~text
approve：按原参数执行
reject：拒绝执行并携带原因
edit：修改参数后执行
respond：不执行工具，直接返回人工响应
~~~

GraphHarbor 规范：

- `interrupt()` 是 graph 层原语。
- PostgreSQL checkpoint 是恢复前提。
- `thread_id` 同时是恢复和权限边界。
- 外部 `commands`/SDK `command` 最终映射到 `Command(resume=...)`。
- HITL 和用户主动 cancel 对外都使用官方 `interrupted` 状态；通过 interrupt payload、事件和内部 reason 区分，不能新增非官方 `cancelled` run status。
- 支持同一 run 中多个顺序 interrupt。
- interrupt payload 必须 JSON 可序列化、可审计。
- resume 必须幂等，重复提交不能重复执行工具。

官方参考：

- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangChain HITL](https://docs.langchain.com/oss/python/langchain/human-in-the-loop)
- [Agent Server HITL](https://docs.langchain.com/langsmith/add-human-in-the-loop)
- [LangGraph event streaming](https://docs.langchain.com/oss/python/langgraph/event-streaming)

### D13：P0 事件能力

- 主图/子图 lifecycle：started/completed/failed/interrupted。
- `messages` token/content-block 流。
- `values` 状态快照。
- `updates` 节点更新。
- `custom` 业务事件。
- `debug` 调试事件。
- `events` 原始/投影事件。
- interrupt、resume、错误和终止事件。
- sequence/cursor 和断线恢复。

事件测试必须断言 `run_id`、`thread_id`、event type、namespace/path、seq、interrupt payload 和最终状态。

## 5. 数据和可靠性推荐方案

### D14：PostgreSQL

- 每个部署使用独立 database；实例可以共享，database 不共享。
- 生产使用独立 runtime 角色，migration 使用单独管理角色。
- migration 由发布前/滚动发布前的独立 job 执行，服务启动只检查 schema 版本。
- 连接使用 pre-ping、超时、pool 上限和故障告警。
- 开启备份、PITR 和恢复演练。
- 明确 thread/run/checkpoint/store 的保留和清理策略。
- PostgreSQL 是 run 状态、checkpoint 和审计事实源；Redis 不是事实源。

### D15：Redis

- 用于 job queue、跨实例 Pub/Sub、取消信号和短期 replay buffer。
- PostgreSQL 保存最终 run 状态和 checkpoint。
- 每个部署至少独立 Redis database，并使用唯一 key prefix。
- key prefix 必须存在，因为 Redis Cluster 只有 database 0。
- 生产使用 TLS、认证、连接超时和健康检查。
- replay/queue key 不得被无意 eviction 清理。
- 生产优先托管 Redis、Sentinel 或 Cluster；单机仅用于开发/小规模。
- Redis 暂时不可用时，停止接收需要实时后台执行的新 run，不静默丢任务。
- 断线恢复以 replay cursor + PostgreSQL terminal status 兜底。

### D16：run/cancel/retry

~~~text
pending → running → success
                   ↘ error
                   ↘ timeout
                   ↘ interrupted (HITL / cancel / multitask / shutdown)
~~~

推荐：

- 创建 run 使用幂等键，避免网络重试产生重复任务。
- 同一 thread 默认遵循官方 `multitask_strategy="enqueue"`；同时支持 `reject`、`interrupt`、`rollback`，不能用私有锁替换 SDK 语义。
- cancel 幂等；默认异步，`wait=true` 等待完成；终态 run 不得被晚到 cancel 覆盖。
- `action=rollback` 除了停止 run，还删除 run 和关联 checkpoint；`action=interrupt` 只停止执行。
- 对外 run 状态使用官方枚举；内部可记录 `hitl_interrupt`、`cancel_requested`、`shutdown_requeue` 等 reason。
- live worker 的 cancel 通过 Redis 控制消息，API 做数据库兜底。
- worker 崩溃由 lease/reaper 恢复，从最近 checkpoint 继续。
- retry 只针对可恢复基础设施错误；业务错误、用户拒绝和 HITL 等待不自动 retry。默认最多 3 次，指数退避 1/2/4 秒并设上限。
- graceful shutdown 时 requeue，不把正常部署记为失败。

Aegra 的 lease、heartbeat、reaper、cross-instance cancel、drain/requeue 是推荐参考。

## 6. 部署、扩容、安全和观测

### D17：部署

支持 systemd、supervisord 或其他主机进程管理方式。必须提供：

1. 不依赖 Docker 的本地 API、worker、migration 启动方式，作为首阶段验收基线。
2. 可被 systemd、supervisord 或其他进程管理器调用的通用启动命令。

生产拓扑：

~~~text
API/SSE instances：无状态，可横向扩展
Worker instances：消费共享队列，可独立扩展
PostgreSQL：独立持久化服务
Redis：独立高可用服务
Load Balancer：SSE、健康检查、TLS
~~~

### D18：多 worker

- API 负责校验、持久化、入队和 SSE。
- worker 从 Redis queue 领取 run。
- PostgreSQL lease 保证单 run 单 worker。
- heartbeat 延长 lease，reaper 回收失联 worker。
- API/worker 都可多实例。
- SSE 连接落到任意 API 实例，事件通过 Redis 汇聚。
- 优雅退出先停止接收新 run，再 drain/requeue。

### D19：认证、CORS、网络

- API gateway 负责 TLS、CORS、限流和 body 限制。
- Agent Server 不暴露数据库、Redis 或管理端口。
- platform-api 到 runtime 使用独立内部 credential。
- CORS 使用明确 allowlist，禁止生产 `allow_origins=["*"]`。
- custom routes 默认纳入认证和授权；health/readiness 单独列出。
- 日志记录 request_id、run_id、thread_id、tenant_id、principal_id，不记录 token 和敏感 prompt。

### D20：可观测性

- OpenTelemetry traces，导出到用户选择的 OTLP backend。
- 结构化 JSON 日志。
- Prometheus metrics。
- 指标覆盖 run 生命周期、queue depth、claim/retry/reaper、SSE reconnect、event lag、HITL wait、PG pool、Redis latency。
- trace 跨 API → Redis queue → worker → graph → custom route。

## 7. 版本、迁移和发布

### D21：版本发布

- `graphharbor` 和 `graphharbor-runtime` 锁步版本。
- 每个版本绑定已测试的 LangGraph 依赖。
- 协议、runtime schema 或 custom route 语义变化必须有迁移说明。
- 先 TestPyPI，再正式 PyPI。
- 发布前通过 P0 graph、协议、多 worker、故障和安全回归。
- 使用兼容矩阵记录 GraphHarbor、LangGraph、SDK、Python、PostgreSQL、Redis 版本。

### D22：迁移

~~~text
当前 langgraph dev
        ↓
GraphHarbor compatibility mode
        ↓
本机 PostgreSQL + Redis
        ↓
服务器进程管理
        ↓
灰度流量
        ↓
正式切换
~~~

前端第一阶段只切 API base URL 和兼容 SDK，不立即改事件投影。`langgraph dev` 的内存数据不承诺自动迁移；生产 PostgreSQL 数据必须有备份、schema 和回滚策略。

## 8. 六项冻结与剩余开放问题

六项冻结的逐条建议、事实证据和 owner 确认项见
[`production-freeze-decisions.md`](production-freeze-decisions.md)。这里不再重复维护第二份状态机或兼容清单。

冻结后仍需补充的运行参数只有：

1. 是否存在必须迁移的生产 thread/checkpoint 数据。
2. 目标并发 run、SSE 长连接、P95 首事件延迟、P95 完成时间和数据保留期。
3. 是否已有 OTLP collector、Prometheus、Grafana、Langfuse 或 Phoenix。
4. 五个 P0 graph 是否全部使用真实外部依赖做验收，还是为部分依赖提供可控 fake。

六项已经确认，可以进入基线、升级实验和切片实现；改变公共协议、认证或状态机的代码仍必须通过对应 OpenSpec review、官方 SDK/REST/SSE 契约和 E2E 验收。
