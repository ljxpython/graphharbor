# Worker 取消传播与停止确认实现

## 时间与关联任务

2026-10-07；Task 0.1~3.2、4.1/4.2。进度以 [tasks.md](../tasks.md) 为准。

## Runtime 执行与持久化

- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/redis_stream.py`：`StreamManager.add_control_queue()`、`wait_control_ready()`、`_pubsub_mux_loop()` 复用共享订阅；收到 Redis 订阅确认后复查控制 key/PG，重连向活跃 queue 发 recheck，结束时移除队列。Core JSON 与内部字节 action 共用解析，done/未知消息不触发取消。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py:373`：`ProductionWorker._listen_for_cancel()` 接入执行等待；factory 前建立监听并复查意图。`run_once()` 取消 execution 后 await stream/子任务/资源及 event buffer，`_confirm_shutdown()` 对瞬时提交失败重试；heartbeat 延后到收尾提交之后回收。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py:430`：`RunRepository.renew()`、`finish()` 保留清理阶段 owner/generation 续租，持久单 terminal 与 lease/Thread 一起提交。`stopped_event()` 排除 `execution_stopped=false`；`requeue_expired()` 只写 fence，不冒充停止，已取消 Run 不重新入队。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py:80`：`rollback_receipt()`、`complete_rollbacks()` 保存 `cancel.stopped` 和 `cancel.receipt` 停止/恢复完成回执；恢复失败可由后续进程重试。使用既有事件表，无 Schema 迁移；pending rollback 保留原 Thread checkpoint。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/graph_executor.py`：复用已有 `AuthUser` 保存 verified user，使实际子图可用属性及字典访问读取身份；不引入平台业务类型。

关键顺序由“公开 interrupted → 等 heartbeat → 清理”改为：

```text
PG 提交取消意图 → after-commit Redis 通知
→ listener 唤醒执行 → cancel/await 受管 execution 与资源
→ 当前代际提交停止依据及 lease/Thread
→ fanout/done → 回收监听和 heartbeat
```

## 网关与使用方

- `libs/langhost/src/langhost/core_api.py`：`_cancel_row()` 对运行中取消保留 lease，不发停止 terminal；`runs_cancel()`/批量取消统一提交后通知，wait=false 空 body 202。`_wait_for_run()` 依据持久停止/rollback receipt 等待，超时或无法确认 503。
- `libs/langhost/src/langhost/streaming.py`：SSE/join 结束条件复用持久停止依据，提前 interrupted 不结束流。
- 使用方 `apps/runtime-service/tests/integration/test_model_resilience_worker.py` 记录默认 heartbeat 下 ACK/停止确认/lease 释放和候选次数；`test_model_resilience_composition.py` 增加实际子图清理 barrier。模型被 middleware 浅复制，测试用共享 `seen` 记录辨认候选，保留精确调用次数断言。

## 新增与扩展验证

- `libs/langgraph-runtime-pg/tests/test_worker_cancel_propagation.py`：默认通知、factory/节点/stream/真实子图、订阅窗口/重连、PG 兜底、慢清理续租、提交/rollback 恢复失败、代际/fence、超时/断开、鉴权及并发资源回收。
- `scripts/verify_worker_cancel_protocol.py`、`tests/javascript/cancel.mjs`：锁定官方 HTTP/Python/JS 对照；文件清理 barrier、join 与 SSE，使用 starts 标记检测重执行。
- `scripts/verify_worker_cancel_release.py`：独立进程真实 kill、停止后回退 post41、双向混合版本；`scripts/run_isolated_tests.py` 创建临时 PG/Redis，避免任何现役数据库清理。

结果与命令见 [verification.md](../verification.md)。外部已发送请求、任意同步线程和未注册后台任务不在受管停止保证内；无法获得退出证据时返回未确认。
