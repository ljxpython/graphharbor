# GraphHarbor 生产化路线与验收方案

> 本文是路线摘要。详细架构讨论和开放问题见
> [production-decisions.md](production-decisions.md)；可执行阶段、任务和门禁见
> [production-implementation-plan.md](production-implementation-plan.md)；六项冻结讨论见
> [production-freeze-decisions.md](production-freeze-decisions.md)；分层兼容范围见
> [compatibility-profile.md](compatibility-profile.md)。

## 1. 目标定义

GraphHarbor 的核心目标不是简单替换包名或发布 PyPI，而是：

> 在生产环境中提供接近 `langgraph dev` 的使用体验，同时具备官方 LangGraph Agent Server 的生产能力。

`langgraph dev` 适合本地快速开发，不等于生产部署。GraphHarbor 的生产目标应定义为：

```text
官方 LangGraph Agent Server 能力
        + PostgreSQL 持久化
        + Redis Pub/Sub 与后台运行支持
        + 当前项目 custom routes
        + custom auth
        + HITL / interrupt / resume
        + 子图事件
        + 断线续传
        + 多 worker 与故障恢复
```

GraphHarbor 优先验证官方 `langgraph-api`、`langgraph-sdk`、Studio 和 Agent Protocol 行为；最终由 GraphHarbor 自有的完整 drop-in Agent Protocol server 承担 HTTP/API 边界，用户无需修改 graph、`langgraph.json`、SDK 调用或前端协议。

## 2. 目标架构

```text
前端 / langgraph-sdk / Studio / Agent Chat UI
                    │
                    ▼
          GraphHarbor Agent Server
          官方协议兼容能力
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
官方 Graph API              custom routes
runs / threads / stream     models / tools / platform APIs
       │                         │
       └────────────┬────────────┘
                    ▼
          GraphHarbor Runtime
          PostgreSQL + Redis
                    │
       ┌────────────┴────────────┐
       ▼                         ▼
    PostgreSQL                 Redis
```

核心约束：

1. 不创建第二套 Agent Protocol。
2. 不让业务 graph 依赖 GraphHarbor 的私有 HTTP 实现。
3. 当前应用继续使用 `langgraph.json`、graphs、auth 和 custom routes。
4. 生产能力必须通过 HTTP/SDK 端到端验证，不能只验证 Python 函数。
5. PyPI 发布是最后一步，不是兼容性验证的替代品。

## 3. 分阶段计划

### 阶段 0：建立能力基线

#### 工作内容

记录当前 GraphHarbor 和目标应用的：

- `langgraph-api`、`langgraph-sdk`、`langgraph`、`langgraph-cli` 版本。
- `/info`、`/health`、`/openapi.json` 和实际路由。
- `langgraph.json` 中的 graphs、auth、HTTP app、CORS 和环境变量。
- 当前前端实际使用的 SDK API。
- PostgreSQL、Redis、custom routes 和 auth 的现有测试。

建立能力矩阵：

| 能力 | 当前是否使用 | 是否完成远程验收 |
|---|---:|---:|
| threads / assistants / runs | 是 | 待验证 |
| `client.runs.stream(..., version="v2")` | 计划使用 | 待验证 |
| `/commands` | 未来需要 | 待验证 |
| `/stream/events` | 未来需要 | 待验证 |
| 子图生命周期事件 | 未来需要 | 待验证 |
| HITL interrupt / resume | graph 已使用 | 待验证 |
| 断线续传 | 未来需要 | 待验证 |
| custom routes | 当前已有 | 待远程验收 |
| PostgreSQL persistence | 已有 runtime | 基础测试已通过 |
| Redis Pub/Sub | 已有 runtime | 待服务端验收 |
| 多 worker | 生产需要 | 待验证 |

#### 验证

```bash
uv lock --check
uv run python scripts/check_versions.py
uv run pytest
```

启动后验证：

```bash
curl http://127.0.0.1:31296/ok
curl http://127.0.0.1:31296/info
curl http://127.0.0.1:31296/openapi.json
```

#### 阶段产物

`docs/compatibility-baseline.md`，包含版本、路由、SDK 调用、graph 列表、已通过测试和已知缺口。

### 阶段 1：升级官方 Agent Server 依赖

#### 工作内容

1. 查询并确定目标 LangGraph 稳定版本。
2. 检查 `langgraph-api`、`langgraph-sdk`、`langgraph`、`langgraph-cli` 的兼容约束。
3. 在独立升级分支中更新依赖和 lockfile。
4. 检查 GraphHarbor runtime 是否依赖旧版本内部接口。
5. 记录升级产生的 API 或行为变化。

生产依赖必须锁定明确版本，不能仅使用无上限的宽泛范围。

#### 验证

```bash
uv lock
uv lock --check
uv run python scripts/check_versions.py
uv run pytest
uv build --package graphharbor-runtime
uv build --package graphharbor
```

最小服务必须能够启动，且至少一个 graph 可以执行、持久化和查询。

#### 阶段门禁

- 目标版本可安装。
- runtime migration 可执行。
- PostgreSQL 和 Redis 可连接。
- `/info` 正常返回。
- custom routes 不阻断启动。

### 阶段 2：验证 runtime 与新 Agent Server 的兼容性

#### PostgreSQL

验证 threads、assistants、runs、checkpoint、长期状态、后台任务队列和 migration：

- 空库执行 migration。
- 重复执行 migration 不报错。
- 创建、读取、更新、删除 thread。
- 创建、查询、取消 run。
- run 重启后状态可恢复。
- checkpoint 能写入并继续执行。
- exactly-once queue 语义保持成立。

#### Redis

验证：

- 后台 run 状态广播。
- 实时 stream 转发。
- 多进程 Pub/Sub。
- 客户端断线后的恢复行为。
- Redis 重连行为。
- 多部署使用不同 Redis database 时互不串流。

#### 并发与故障

验证：

- 同一 thread 并发 run。
- 不同 thread 并发 run。
- cancel 正在运行的 run。
- worker heartbeat 过期后 reclaim。
- stale run 清理。
- worker 从 1 个扩展到 2/4 个后行为一致。

#### 阶段门禁

必须同时有 runtime 单测、HTTP API 测试、多 worker 测试，以及 PostgreSQL/Redis 重启场景记录。

### 阶段 3：组合 custom routes 与 runtime lifespan

当前项目通过 `langgraph.json` 配置：

```json
{
  "http": {
    "app": "./runtime_service/custom_routes/app.py:app"
  }
}
```

需要验证并实现以下组合关系：

```text
官方 Agent Server lifespan
        +
GraphHarbor runtime lifespan
        +
runtime-service custom FastAPI lifespan/routes
```

#### 实施顺序

1. 用最小 FastAPI app 验证 startup/shutdown 是否被 Agent Server 调用。
2. 接入 GraphHarbor runtime，验证 migration、连接池和 Redis 初始化。
3. 接入当前 custom routes。
4. 接入当前 auth、CORS、model capabilities 和 tools routes。
5. 验证服务关闭时资源正常释放。

#### 阶段验收

以下请求必须同时成功：

```text
GET  /custom-route       200
GET  /info               200
POST /threads            200
POST /runs/stream        200
HITL interrupt           返回可恢复的 interrupt
resume                   能继续原 run
shutdown                 lifespan 正常清理
```

如果 custom app 覆盖官方或 runtime lifespan，必须在本阶段修复，不能带入发布版本。

### 阶段 4：验证 v2/v3 流式协议

需要区分三层 API：

```python
# graph 内部更新流
graph.stream(..., version="v2")

# graph 内部事件流
graph.stream_events(..., version="v3")

# 远程 Agent Server SDK
client.runs.stream(..., version="v2")
```

远程 v2/v3 是否可用，取决于 SDK、Agent Server 和 HTTP/SSE 协议的组合，不能只根据本地 graph API 判断。

#### 协议测试矩阵

| 场景 | 验证内容 |
|---|---|
| 普通 stream | updates/messages 可接收 |
| token stream | 事件顺序和内容正确 |
| 多模式 stream | 事件类型可以区分 |
| 子图 | namespace、路径、生命周期完整 |
| HITL | interrupt payload 可解析 |
| resume | command resume 后继续原 run |
| 断线 | 重连后事件可恢复或明确去重 |
| events | event payload 完整 |
| custom route | 独立路由不影响 SSE |

#### 验收断言

不能只断言 HTTP 200，必须检查：

- `run_id`、`thread_id`。
- event type。
- namespace / subgraph path。
- interrupt payload。
- resume 后状态。
- final state。
- error event。
- 断线续传位置。

### 阶段 5：使用当前真实 graph 做端到端验证

优先级如下：

#### P0

- `assistant`
- `test_case_agent_v2`
- `customer_support_handoffs_demo`

#### P1

- `deepagent_demo`
- `personal_assistant_demo`

#### P2

- `sql_agent`
- `research_demo`
- `skills_sql_assistant_demo`

每个 graph 至少验证：

```text
graph discovery
assistant lookup
thread create
run create
stream
state persistence
history
interrupt/resume
error handling
custom auth
```

P0 graph 全部通过后，才进入生产部署验证；P1/P2 graph 的实验性依赖不能阻塞核心服务发布，但必须在兼容性报告中明确标注。

### 阶段 6：生产部署与故障验证

推荐生产拓扑：

```text
GraphHarbor Agent Server：systemd、supervisord 或其他主机进程管理
PostgreSQL：独立服务或托管数据库
Redis：独立服务或托管 Redis
```

数据库和 Redis 不应与 Agent Server 共用同一个不可独立恢复的容器生命周期。

#### 必需能力

- 首阶段提供本地 API、worker、migration 启动方式；不提供 Docker Compose 或 Kubernetes 资源。
- health/readiness 检查。
- 可重复执行的 migration。
- 明确启动顺序。
- 环境变量和 secrets 校验。
- CORS 白名单。
- authentication/authorization。
- 结构化日志和 trace。
- graceful shutdown。
- worker 数量配置。
- PostgreSQL 备份与恢复说明。
- Redis 故障处理说明。
- 版本回滚说明。

#### 故障场景

```text
1 worker → 2 worker → 4 worker
重启 Agent Server
重启 Redis
重启 PostgreSQL
终止一个 worker
客户端断网后恢复
重复提交同一个 run
取消正在运行的 run
```

每个场景必须记录预期结果、实际结果和恢复方式。

### 阶段 7：发布 GraphHarbor

只有以下条件全部满足后才允许发布：

```text
官方 Agent Server 版本已确定
runtime 兼容测试通过
custom routes + lifespan 通过
v2/v3 协议测试通过
当前 acceptance P0 fixture 通过；外部业务 graph 需在其源码仓库单独验收
多 worker 通过
故障恢复通过
生产部署文档完成
```

发布包：

```text
graphharbor
graphharbor-runtime
```

两个包需要锁步版本，并在文档中声明对应的 `langgraph-api` 兼容版本。Trusted Publishing、TestPyPI 和正式 PyPI 发布都属于外部状态变更，必须在发布前单独确认。

## 4. 分支建议

按实际需要拆分，不提前制造大量分支：

```text
main
├── chore/baseline-agent-server
├── feat/upgrade-agent-server
├── feat/runtime-lifespan-composition
├── test/protocol-v2-v3-compatibility
├── test/runtime-service-e2e
├── feat/production-deployment
└── release/graphharbor-first-preview
```

当前优先只使用一个工作分支；完成一个阶段并通过门禁后，再决定是否拆分后续分支。

## 5. 最终完成定义

GraphHarbor 只有在以下条件全部满足时，才可以称为“生产级 LangGraph Agent Server runtime”：

1. 使用已验证的目标 Agent Server 版本。
2. `langgraph.json` 正常加载。
3. P0 graph 可发现、执行和持久化。
4. PostgreSQL 持久化正常。
5. Redis Pub/Sub 正常。
6. custom routes 和 custom auth 正常。
7. custom lifespan 与 runtime lifespan 正确组合。
8. 远程 `client.runs.stream(..., version="v2")` 正常。
9. 事件流可支撑未来事件投影。
10. HITL interrupt/resume 正常。
11. 子图事件可观察。
12. 断线可恢复。
13. 多 worker 行为一致。
14. 服务重启不丢失 run 状态。
15. migration 可重复执行。
16. 生产部署可重复。
17. 故障场景有明确恢复策略。
18. CI 自动执行核心验证。
19. 最后才发布 PyPI。

## 6. 当前状态与下一步

已完成：

- GraphHarbor fork、项目改名和基础打包。
- PostgreSQL + Redis runtime 基础测试。
- 本地 PostgreSQL 用户、数据库和 Redis 环境准备。
- CI、构建和 Trusted Publishing 工作流基础配置。
- OpenSpec `implement-production-agent-server` 已安装并建立，当前 51 个任务中完成 44 个。
- LangGraph 目标版本基线和兼容性脚本已通过。
- 生产 wheel 已去除硬依赖 `langgraph-api`；旧版本仅保留 compatibility extra。
- 自有 API/migration 入口、worker fail-closed guard、组合 lifespan、Principal/JWKS、run
  状态机、lease primitive 和 PostgreSQL 002 增量 schema 已落地。
- 自有 server 已提供 health/discovery、assistants、threads、run CRUD/cancel、v2 SSE 和
  Protocol v2 thread events；当前核心回归与生产契约测试通过。
- 官方 Python/JavaScript SDK Core、REST 契约、runtime-service custom routes/lifespan 与五个
  P0 graph 的历史外部验收已有记录；当前仓库新增 acceptance fixture 的真实 HTTP/SDK E2E，
  但不能替代外部 runtime-service 源码的重新验证。
- 本地故障验收已覆盖 API/worker/PG/Redis 重启、worker 滚动停机与替换 worker 接管、队列积压、
  SSE `Last-Event-ID` replay；重启窗口的 run 持久化为 `success`，基础设施重试计数为 1、1、2。

尚未完成：

- 真实网络长连接重连和 v3 graph typed projection E2E。
- Python 3.11/3.12/3.13 CI、构建、TestPyPI 隔离安装及发布回滚验收。

下一步完成发布门禁。当前代码尚未达到最终生产发布门禁。
