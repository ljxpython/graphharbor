# 事务化 checkpoint 修剪与回滚

时间：2026-09-24。状态：partial；实现及本地核心验证完成。

## 改动与原因

| 文件（相对仓库） | 关键函数/结构 | 改动 |
| --- | --- | --- |
| libs/langhost/src/langhost/core_api.py | threads_prune、_cancel_row、runs_cancel、runs_cancel_many | 修剪只消费授权 rows；回滚共享事务函数；运行中保留意图；wait 等待持久清理；批量逆序 |
| libs/langhost/src/langhost/server.py | _run_cancel | 旧入口委托共享 handler，移除整 thread 删除 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py | Threads.prune | 旧 profile 先走 delete 授权事件，再调用同一修剪函数 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py | capture_baseline、rollback_run、complete_rollbacks、prune_checkpoints | 基线、精确恢复、恢复重试、namespace 与 Delta 祖先保留 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint.py | FencedPostgresSaver | 写入在同一 psycopg 事务中锁 run/thread 并验证租约；metadata.run_id 由可信上下文写入 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py | claim_next、renew、fail | 首次 claim 捕获基线；待回滚 thread 不接新任务；终态不续租/不被失败覆盖 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py | run_once、reap_once | 注入可信 writer 上下文；等待图任务退出后回滚；重启恢复 |
| libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py、migrations/versions/007_checkpoint_baselines.py | RunCheckpointBaselineRow | 新表，不修改上游 checkpoint 表 |

原路径：删除 RunRow → after_commit 整 thread 删除，无法保证失败原子性。
新路径：捕获基线 → 提交回滚意图并隔离写入 → 停止/过期 → 同事务恢复 checkpoints、writes、投影 → 删除目标 run。

恢复实现会在持有维护锁的事务内替换该 thread 的 checkpoint/write 行集为原始基线，提交时历史精确复原；不是删除全部历史的降级路径。Blob 按不可变版本保留，防止破坏祖先或共享数据。

## 成本与限制

全量历史基线会随历史增长而增加 claim I/O 和存储；没有规模基准，不能承诺生产性能。当前保留共享与孤立 blobs，不提供回收策略。旧运行、被 prune/显式 state 更新作废的基线、存在后继运行依赖时返回 409；不猜测恢复。图外部工具副作用与 Store 写入不纳入此事务。

## 部署与回退

先停止接收维护请求并停旧 worker，执行 `graphharbor-runtime-migrate upgrade`，确认 revision 为 `007_checkpoint_baselines`，部署同版本 API/worker 后恢复流量。禁止旧 worker 与新 API 混用，旧 saver 不遵循 fence。旧数据不会自动补造基线。

数据库降级仅已在隔离 schema 验证；生产降级会丢基线，不作为在线回滚手段。若需撤回，先停止维护入口和 worker，保留 007 数据并使用显式拒绝 rollback/keep_latest 的安全版本；不能直接回退到原始整 thread 删除实现。未执行生产迁移、发布、git commit。
