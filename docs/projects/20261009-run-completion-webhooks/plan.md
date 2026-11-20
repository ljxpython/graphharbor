# 方案

## 目标与边界

对齐 LangGraph Server 的 Run/Cron webhook、静态 headers/env 模板、URL 策略、顶层 allowed_fields 和 disable。标准 Run JSON 与受管 `run.terminal` profile 明确区分，共用一套持久 sender。业务原因只通过应用注册的纯 projector 输出，来源只来自已认证 principal 的 opaque callback context。

## 落点

- `libs/langhost/src/langhost/server.py`：启动配置验证及通用 projector 装配；CLI/API 使用同一配置。
- `libs/langhost/src/langhost/core_api.py`：Run/Cron admission 验证 URL，保存可信 context，公开对象移除私有字段。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py`、migration 012：独立 delivery snapshot，无 Run/Thread 外键；按 profile/run 唯一。
- `run_store.py::record_event`：与真实终态事务写 delivery；execution_stopped=false 的 fence 不投递，后续确认沿同一终态 event_id。
- `production_worker.py`：最终异常的纯安全投影，在工厂失败、模型失败和超时统一出口使用；不新增 graph middleware。
- `webhook_delivery.py`：短事务 SKIP LOCKED claim/lease、网络事务外发送、固定 body、fresh timestamp HMAC、指数重试、dead-letter、replay、指标；崩溃只重发，不重跑 Agent。

## 契约

标准 profile 保存公开 Run payload，按 status ACK，兼容配置策略；受管 profile 为 `schema_version/event_id/event_type/origin_ref/run_id/thread_id/graph_id/status/reason/outcome/execution_stopped/sequence/occurred_at`，应用安全码只在 `outcome` 中，由应用 projector 输出。签名为 key_id、timestamp、POST、目标 path、raw body 以换行连接，SHA256 HMAC。受管固定目标，2xx 必须返回同 event_id ACK，不跟随重定向。

## 验证与发布

真实隔离 PG/Redis 验证 finish/fail/factory/timeout/cron/cancel/reaper/rollback/delete、并发 dispatcher、响应丢失与重启恢复。普通 SDK 和受管 profile 分开验收。完成代码质量/回归/构建后读取本地 `~/.my_best/.env` 的发布凭据，直接发布匹配双包并冷安装核验；不输出凭据。
