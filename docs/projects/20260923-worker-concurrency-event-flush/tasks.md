# Worker 并发与事件刷盘治理 - 任务拆分

## Phase 1：并发调度

- [x] **1.1 参数传递：** `libs/langhost/src/langhost/cli.py` 的 `worker_command()` 显式传递正整数并发数；CLI 选项优先于环境变量，默认 1。2026-09-23 完成。
- [x] **1.2 独立槽位：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` 的 `run_worker()`/`ProductionWorker.run_forever()` 创建 N 个独立槽位、唯一 owner、单 reaper 和统一关闭。2026-09-23 完成。
- [x] **1.3 回归：** 覆盖 N=1/N=4、跨 thread 并发、同 thread 串行、关闭重排和既有租约回收。2026-09-23 完成。

## Phase 2：事件刷盘

- [x] **2.1 基线：** 用 v3 `messages/content-block-delta` fixture 测量 PG-only 与 PG+Redis 的 1000 条事件对照，并运行 11208 条完整链路；消费反压未单独定量，待生产观测。2026-09-23 完成。
- [x] **2.2 有界微批：** 修改 `production_worker.py`、`run_store.py` 与 `redis_stream.py`，按 run 保序批量持久化、提交后 pipeline fanout，并实现非增量/终态/关闭 flush 屏障。2026-09-23 完成。
- [x] **2.3 契约回归：** 覆盖事件 ID、sequence、内容、顺序、终态、跨实例 Redis fanout、PG 失败反馈与既有持久重放测试。2026-09-23 完成。

## Phase 3：真实链路与边界

- [x] **3.1 真实模型烟测：** 使用仓库外 `~/.my_best/.env` 的凭据，通过验收图配置 `miaomiaoai` / `deepseek-v4.1-flash`；完成双会话同时流式输出及 459 条 `messages` 的 Agent 长输出。2026-09-23 完成。
- [x] **3.2 边界检查：** 确认改动不引入供应商、模型、业务 tool 或业务 trace 字段；存量字段见 [open-issues.md](open-issues.md)。2026-09-23 完成。
- [x] **3.3 结论：** 更新 `verification.md` 的实际数据与完成状态。2026-09-23 完成。

## 完成记录

本地开发与验证于 2026-09-23 完成；`0.13.0.post32` 已发布 PyPI。生产部署及部署后 p95 观测仍待执行。
