# 通用 Webhook 实施

日期：2026-10-09。相关任务 G1-G4；进度以 tasks.md 为准。

- `webhooks.py`：部署 headers/env/URL/字段策略、纯 projector、公开 Run JSON 与受管 v1 snapshot 分离。callback_context 来自已认证 principal 和持久签名 envelope；没有平台用户/project/供应商逻辑。
- `models.py`、迁移 `012_run_callback_deliveries.py`：独立 delivery，不绑定 Run/Thread/event 外键；删除或 rollback 仍保留快照。
- `run_store.py`、`production_worker.py`、`checkpoint_mutations.py`：终态与 Outbox 同事务；租约回收不冒充执行退出，awaiting_stop 可运维查询，原 Worker/运维确认同 generation 后复用 event_id；rollback 独立确认，不重跑图。
- `webhook_delivery.py`：skip-locked claim、generation CAS、60s lease/40s 总发送时限，4 个槽；HTTP 无数据库长事务；24h 截止、死信、显式 replay/discard、30d 已送达清理。重新投递用当前 key、新时间戳，同 body/event。
- 传输使用系统 CA、验证 DNS 返回地址并连接同一数字 IP；普通 profile 配置化 redirect，跨 origin 去掉静态凭据；受管固定 HTTPS 目标、不 redirect、同 event_id 严格 ACK。原始 response/body 不写日志。
- `langhost/core_api.py`、`server.py`、`cli.py`：Run/Cron 入口接线、私有字段过滤、部署启动校验、dispatcher 生命周期与 `graphharbor webhooks` 运维入口。
- `schema_setup.py`：真实双 Worker 启动复现了 advisory 阻塞 SELECT 与 CREATE INDEX CONCURRENTLY 的 snapshot 死锁。改短事务 pg_try_advisory_lock 等待，新增并发建索引测试；不更改持久表语义。

验证：真实 PostgreSQL 的事务 rollback、删除快照、取消/rollback、ACK 后崩溃、轮换、重放、deadline；DNS/TLS/redirect 契约；使用方 API/双 Worker/PG/Redis 链路。完整结果及失败修复留在 verification，不把 skip 当 pass。
