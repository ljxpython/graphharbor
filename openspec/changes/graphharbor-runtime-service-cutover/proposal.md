## Why

`runtime-service` 目前仍依赖原有 Agent Server 运行面，GraphHarbor 虽已具备 PostgreSQL、Redis、Worker、SSE、HITL 和签名 RuntimeContext 的主要实现，但还没有一份把两者正式接起来的生产切换契约。现在需要把 GraphHarbor 从“兼容性候选”推进为可灰度、可回滚、可审计的正式替代品。

## What Changes

- 新增 GraphHarbor 承载 `runtime-service` Agent factory 的正式部署和切换契约。
- 将 `platform-api -> GraphHarbor -> runtime-service graph` 定义为正式执行链，禁止客户端绕过控制面直连 graph。
- 要求生产 Run 使用版本化、签名、绑定 tenant/project/thread/run/principal/policy 的 RuntimeContext。
- 要求 PostgreSQL 作为 Run、Checkpoint、Lease、Event 的事实源，Redis 只承担队列、通知、取消和 replay 协调。
- 补齐双 Worker、Worker 崩溃接管、迟到 finalize、SSE replay、HITL、DeepAgent workspace 和 Subagent 能力收缩的真实验收。
- 补齐 Langfuse/OTLP 字段 allowlist、脱敏、exporter 故障隔离、指标和发布回滚证据。
- **BREAKING** 正式生产入口不再使用匿名执行、客户端身份覆盖或未签名 RuntimeContext；不兼容的旧 Run 只能走受控回退路径。
- 增加 `0% -> 1% -> 10% -> 50% -> 100%` 灰度开关、旧路径回退和 schema 向前兼容规则。

## Capabilities

### New Capabilities

- `runtime-service-cutover`: 定义 GraphHarbor 接管 runtime-service 的部署、权限、数据、观测、灰度、回滚和 readiness 门槛。

### Modified Capabilities

- `agent-server-protocol`: 明确 runtime-service factory、RuntimeContext 和生产错误语义必须由 GraphHarbor 承接。
- `deployment-runtime`: 增加双进程部署、健康检查、migration、灰度开关和回滚约束。
- `event-streaming`: 增加跨 Worker/跨网络 replay、游标和终态事件的正式验收要求。
- `principal-auth`: 增加 GraphHarbor 与 platform-api delegation JWT 的生产边界。
- `run-execution`: 增加迟到 Worker、幂等 finalize、cancel/recovery 的正式替代要求。
- `runtime-persistence`: 增加 runtime-service Run/Checkpoint/Workspace 恢复和 schema 兼容要求。

## Impact

- GraphHarbor：`libs/langhost`、`libs/langgraph-runtime-pg`、migration、生产 CLI、验收脚本和 runbook。
- `ai-agent-platform/apps/runtime-service`：`langgraph.json`、Agent factory、DeepAgent/MCP 资源生命周期和 observability 配置。
- `platform-api`：runtime gateway 的 base URL、delegation JWT、灰度路由、Run ID 映射和回退开关。
- 外部系统：PostgreSQL、Redis、模型 Provider、MCP 服务、Langfuse/OTLP collector、反向代理或受控网络隧道。
- 该变更先建立和验证切换能力；所有硬门槛通过且 owner 批准前，不改变默认生产流量。
