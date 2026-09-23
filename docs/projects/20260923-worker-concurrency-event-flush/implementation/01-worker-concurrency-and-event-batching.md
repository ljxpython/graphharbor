# Worker 并发与事件微批实现

## 改动时间

2026-09-23

## 相关任务

Task 1.1-1.3、2.1-2.3、3.1-3.3。

## 改动文件与理由

- `AGENTS.md`：固定通用 Agent Server 边界、存量业务字段待办、真实模型密钥查找与发布限制；无密钥值进入仓库。
- `libs/langhost/src/langhost/cli.py:474`：worker CLI 显式传递并发数；CLI 选项优先于 `N_JOBS_PER_WORKER`，未指定时环境变量优先于默认值 1。原先生产入口忽略参数。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py:917`：按并发数创建独立 owner、repository 和执行循环，只启一个 reaper，统一停止并等待槽位退出。原先始终只有一个串行循环。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py:114`：只缓冲 v3 `messages/content-block-delta`，32 条或 50ms 刷新，非增量和终态前强制 flush。原先每条增量同步 PG/Redis；取消状态现由已有心跳轮询并反馈给执行任务，避免每条增量额外读 Redis/PG。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py:549`：同一 run 的微批只锁一次游标、取一次最大序号、flush 一次，仍逐条创建持久事件和单调 sequence。不改数据库模式。
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/redis_stream.py:587`：对同批已提交事件用 replay `XADD` pipeline 和 run/thread `PUBLISH` pipeline；本地与跨实例订阅者仍逐条按序接收。
- `libs/langgraph-runtime-pg/tests/test_production_contract.py`、`libs/langhost/tests/test_cli.py`：覆盖参数、四槽位并行与关闭、事件提交顺序、1.1 万条增量、跨实例 Redis fanout。

## 兼容性与限制

对外事件 ID、payload、namespace、sequence、终态和重放契约保持不变；缓冲最多 32 条，数据库提交失败会反馈给 run。Redis fanout 失败沿用已有的告警与 PG 重放语义。供应商和模型只在验收环境注入，不进入核心包。`model_id` 等存量字段见 [待办](../open-issues.md)，本次未删除。

## 验证

本地隔离 PostgreSQL、现有 Redis、官方 SDK 和真实模型的结果见 [verification.md](../verification.md)。未执行发布。
