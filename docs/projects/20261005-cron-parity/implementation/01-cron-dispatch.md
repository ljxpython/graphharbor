# Cron 调度实现记录

日期：2026-10-05。对应任务：T1-T5。

- `libs/langhost/src/langhost/core_api.py` 的 `cron_create()`、`cron_update()`、`crons_search()` 对齐锁定 SDK 的校验、时区、搜索和 `cron_id`，保存创建身份快照且不将内部身份字段返回客户端。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py` 的 `dispatch_due_crons()` 使用 PostgreSQL 行锁领取到期 cron，逐条保存点创建持久 Run，提交后入队；失败回滚并在后续轮询重试。每次 Run 签发绑定 Run/Thread 的运行时身份 token。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production.py` 在应用生命周期启动和取消调度任务；不依赖官方私有调度器。
- `libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py` 在指定隔离库验证 SDK、worker、DST、截止时间、并发、失败、重启、生产身份和租户隔离。

影响：两个包均有本地未发布改动；未变更数据库 Schema。验证结果见 `../verification.md` 的 Final 区块。
