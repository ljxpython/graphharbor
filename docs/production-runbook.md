# GraphHarbor 本地生产运行手册

生产 profile 不依赖 `langgraph-api`、LangSmith License Key、Docker Compose 或 Kubernetes。PostgreSQL 和 Redis 必须是独立的主机服务或托管服务。

## 启动顺序

```bash
export DATABASE_URI='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/graphharbor'
export REDIS_URI='redis://127.0.0.1:6379/0'
export GRAPHHARBOR_RETRY_BASE_SECONDS=1
export GRAPHHARBOR_REAPER_INTERVAL_SECONDS=5

# 只执行一次或在发布 job 中重复执行，必须先于 API/worker。
graphharbor migrate upgrade

# API
graphharbor serve --host 127.0.0.1 --port 31296 -c langgraph.json

# 公共 graph executor 完成后，另一个进程启动生产 worker
graphharbor worker --n-jobs-per-worker 1

# 当前仅可用于旧实现对比，不得用于生产
graphharbor worker --compatibility-spike --n-jobs-per-worker 1
```

生产环境设置 `GRAPHHARBOR_ENV=production` 后，API 和 custom routes 只接受
platform-api delegation JWT；issuer、audience、JWKS URL 必须同时配置。`tenant_id`
和 `project_id` 始终从 JWT Principal 获取，客户端请求体中的覆盖值会被拒绝。
`GRAPHHARBOR_JWT_ALGORITHMS` 默认只允许 `RS256`；只有明确配置算法时才允许对称密钥模式。
生产 worker 使用公共 LangGraph executor、PostgreSQL lease/reaper 和最多三次基础设施重试；
v2 SSE、Protocol v2 和 worker 对 LangGraph `Runtime(ServerInfo)` 的身份注入已可用；v3 typed
projections、完整 graceful drain 和多实例故障验收仍必须通过后才能作为最终生产发布。

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
