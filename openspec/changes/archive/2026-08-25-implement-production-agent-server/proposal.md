## Why

GraphHarbor 当前只有 PostgreSQL/Redis runtime 基础，仍依赖旧版 `langgraph-api` 的内部接口，无法在无 LangSmith 托管和无 `LANGGRAPH_CLOUD_LICENSE_KEY` 的条件下提供官方 Agent Server 的生产协议。当前项目还依赖 custom routes、delegation JWT、HITL、子图事件、批量 run 控制和多 worker 故障恢复，必须把这些能力收敛成一个可由官方 SDK/REST/SSE 直接使用的自有运行时。

六项生产冻结已经完成：Core 兼容范围、LangGraph 版本、真实调用清单、Principal/auth、run/HITL/cancel 状态机和本地验收环境均已确定，因此现在进入完整生产重写具备前置条件。

## What Changes

- 新增无 License Key 的 GraphHarbor Agent Server 协议层，兼容标准 `langgraph.json`、官方 Python/JavaScript SDK 和 REST/SSE。
- 新增 Core 资源面：assistants、threads/state/history/count/copy/prune、runs/wait/stream/join/cancel/batch/bulk-cancel、cron、v2 stream、Protocol v2 events、v3 event projections、HITL 和子图生命周期。
- 将 PostgreSQL 作为 thread/run/checkpoint/lease 的事实源，Redis 用于队列、Pub/Sub、cancel 信号和 replay。
- 新增 API/worker 分离、lease/heartbeat/reaper、retry、graceful drain/requeue 和跨实例事件恢复。
- 将 platform-api delegation JWT 规范化为唯一生产集成凭据，并让 Agent Server 与 custom routes 共用 Principal。
- 保留当前 `langgraph.json`、全部 graph、custom routes、RuntimeContext 和 middleware 契约。
- 提供本地 API、worker、migration 启动方式，连接独立 PostgreSQL 和 Redis 服务。
- 锁定 Python 3.13 生产主线，CI 验证 Python 3.11/3.12；锁定目标 LangGraph/SDK 兼容矩阵。
- **BREAKING**：不再把旧版 `langgraph-api` 私有模块或 License Key 作为最终生产依赖。
- **BREAKING**：对外 run status 不新增 `cancelled`，cancel 和 HITL 均按官方 `interrupted` 暴露，内部以 reason 区分。

## Capabilities

### New Capabilities

- `agent-server-protocol`: 官方 Agent Server 资源、REST/OpenAPI、SDK 和 SSE 协议兼容。
- `run-execution`: run 状态机、HITL resume、cancel、retry、batch、lease 和 worker recovery。
- `event-streaming`: 远程 v2 stream、Protocol v2 thread event stream、v3 projections、子图 namespace/lifecycle 和 replay。
- `runtime-persistence`: PostgreSQL schema、checkpoint、run 状态、Redis queue/PubSub/replay 和 migration。
- `principal-auth`: delegation JWT、Principal、授权过滤、custom routes 认证和跨租户隔离。
- `deployment-runtime`: 本地 API/worker/migration 启动、readiness、graceful shutdown 和部署参考。

### Modified Capabilities

- None. GraphHarbor 当前尚无已发布的 OpenSpec capability；本 change 建立第一版生产契约。

## Impact

- 影响 `libs/langhost`、`libs/langgraph-runtime-pg`、CLI、数据库 migration、Redis runtime 和新增 Agent Server adapter。
- 影响 `apps/runtime-service` 的 `langgraph.json`、custom routes、platform auth、graph discovery 和 E2E harness。
- 影响 `apps/platform-api` 的 LangGraph SDK adapter、stream version 转发、认证 header 和 endpoint compatibility。
- 新增官方 Python/JavaScript SDK、REST、SSE、multi-worker、故障恢复和安全测试。
- 发布包 `graphharbor` 与 `graphharbor-runtime` 继续锁步版本；`store` 保持 Extended，不纳入本 change 的 Core。
