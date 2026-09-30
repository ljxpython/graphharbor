# GraphHarbor 本地生产运行手册

生产 profile 不依赖 `langgraph-api`、LangSmith License Key、Docker Compose 或 Kubernetes。PostgreSQL 和 Redis 必须是独立的主机服务或托管服务。

## 启动顺序

```bash
export DATABASE_URI='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/graphharbor'
export REDIS_URI='redis://127.0.0.1:6379/0'
export GRAPHHARBOR_RETRY_BASE_SECONDS=1
export GRAPHHARBOR_REAPER_INTERVAL_SECONDS=5
export GRAPHHARBOR_LEASE_SECONDS=60
# Optional; unset means no server-enforced Run deadline.
export GRAPHHARBOR_RUN_TIMEOUT_SECONDS=300
export GRAPHHARBOR_RUNTIME_CONTEXT_SECRET='replace-with-a-managed-secret'
export GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER='https://platform.example'
export GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE='graphharbor-worker'

# 业务 JWT 的签发和校验由应用 auth.path 与平台负责。
# GRAPHHARBOR_RUNTIME_CONTEXT_SECRET 只签 API→worker 内部身份快照，不能复用业务 JWT 密钥。

# 只执行一次或在发布 job 中重复执行，必须先于 API/worker。
graphharbor migrate upgrade

# API
graphharbor serve --host 127.0.0.1 --port 31296 -c langgraph.json

# 公共 graph executor 完成后，另一个进程启动生产 worker
graphharbor worker --n-jobs-per-worker 1

# 当前仅可用于旧实现对比，不得用于生产
graphharbor worker --compatibility-spike --n-jobs-per-worker 1
```

生产环境设置 `GRAPHHARBOR_ENV=production` 后，数据入口需要应用通过 `auth.path`
注册 SDK Auth 认证与资源授权。GraphHarbor 只保存通用 `identity`/`permissions` 和签名
API→worker 身份快照；平台的 tenant/project、ACL、模型与工具策略由平台 Auth 和 graph 负责。
内部快照必须使用独立的 `GRAPHHARBOR_RUNTIME_CONTEXT_SECRET`，并配置配对的 issuer/audience。
候选版本在完整授权矩阵、迁移及联合验收完成前不得切换生产。

生产 worker 使用公共 LangGraph executor、PostgreSQL lease/reaper 和最多三次基础设施重试；
v2 SSE、Protocol v2 和 worker 对 LangGraph `Runtime(ServerInfo)` 的身份注入已可用；v3 typed
projections、完整 graceful drain 和多实例故障验收仍必须通过后才能作为最终生产发布。

`GRAPHHARBOR_RUN_TIMEOUT_SECONDS` 必须是有限正数。启用后，Worker 会取消超过 deadline 的
graph 执行，并在 PostgreSQL 中以 `status=timeout`、`reason=timeout` 写入唯一终态事件；该
超时不进入基础设施重试。

## 健康检查

```bash
curl http://127.0.0.1:31296/ok
curl http://127.0.0.1:31296/info
curl http://127.0.0.1:31296/openapi.json
curl http://127.0.0.1:31296/metrics
```

`/ok` 只表示进程存活；数据库、Redis、schema 和 graph discovery 的 readiness
必须由部署探针读取应用 readiness 状态，不能把 liveness 当成可接流量证明。`/metrics`
以 Prometheus 文本格式提供 run、queue、lease/retry/reaper、SSE、HITL、PostgreSQL 和 Redis
信号；GraphHarbor runtime 日志为 JSON（含 `event`、`level`、UTC `timestamp`）。

## 停止和恢复

先停止接收新请求，再等待 worker drain；超过部署的 graceful timeout 后，未完成 run
由 PostgreSQL lease reaper 重新入队。Redis 重启不会删除 PostgreSQL 中的 run 状态、
checkpoint 或事件终态；客户端应使用最后事件 cursor 重连。

2026-08-25 的本地故障验收已验证 API、worker、PostgreSQL 与 Redis 分别重启，worker 滚动
停机、队列积压和 replacement worker 接管均能完成慢 run。Redis 在运行中重启时，run 最终仍为
`success`；PostgreSQL 重启后的 checkpointer 会重连，run 按基础设施重试策略完成。重启窗口中三条
run 的持久化 `retry_count` 分别为 1、1、2，均以 `success` 终态结束。SSE 以
`Last-Event-ID: 1` 重连时返回后续事件和 `end`。

## 流事件保留与回退

候选 worker 默认在 Run 最后更新时间满 24 小时后清理其非终态原始流事件；运行中、终态、checkpoint、Store 和无 Run 事件不按此规则删除。`GRAPHHARBOR_EVENT_RETENTION_SECONDS` 默认 `86400`，设为 `0` 可停止后续清理；`GRAPHHARBOR_EVENT_PRUNE_BATCH_SIZE` 默认 `1000`，每轮 reaper 最多执行 20 个独立事务批次。`/metrics` 暴露 `graphharbor_runtime_events_pruned_total` 与 `graphharbor_runtime_event_prune_duration_ms`。调低 reaper 间隔会增加清理频率，需观察写入延迟与数据库锁等待。

清理前核对数据库目标、终态 Run 数、预计待删行数并保留 PG 备份。已删原始事件不能由 checkpoint 无损重建；回退时先将 `GRAPHHARBOR_EVENT_RETENTION_SECONDS=0` 并重启 worker，再从备份恢复到独立库，按 Run/事件 ID 离线提取，不能直接覆盖已有新事件的在线库。`DELETE` 后表文件不会立即缩小；观察活跃行、`n_dead_tup`、autovacuum 与索引大小，必要时按数据库维护窗口处理。明确过期游标应由客户端改用 Run/Thread state/history 建立新订阅；Protocol 的 HTTP 410 携带 `code=cursor_expired` 和 `recovery=thread_snapshot`。
