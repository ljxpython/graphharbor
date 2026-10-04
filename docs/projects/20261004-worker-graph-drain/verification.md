# Worker 图级优雅停机验证

## 验证环境

- 日期：2026-10-04；双包基线 `0.13.0.post37`。
- PostgreSQL 专用库：`graphharbor_event_retention_verify_drain_20261004`（测试前新建）；Redis 使用 `graphharbor:drain:20261004:full` 前缀。前期定向测试另用了新建的 `graphharbor_drain_20261004_test`。
- 所有会清库的测试均显式设置 `DATABASE_URI`，未连接默认 `langgraph` 库。

## 验证计划

- 图级：确定性双节点图在第一 superstep 后 drain，新实例以同一线程空输入续跑；第一节点只执行一次。
- Worker：SIGTERM、主动取消、drain 宽限超时、无持久线程及租约丢失路径分开断言。
- 协议：同一 Run 的 `running -> pending/shutdown_requeue -> running -> success` 可回放，SSE v2/v3 不新增事件名。
- 持久化：仅在隔离 PostgreSQL/Redis 测试库运行相关集成测试；Delta checkpoint 修剪与状态重建不回归。

## 阶段验证

- Task 1.1：`uv run pytest -q "libs/langgraph-runtime-pg/tests/test_public_runtime.py"` → 18 passed；真实双节点图证明 drain 后空输入续跑且首节点仅执行一次。
- Task 1.2：隔离库中运行 `test_production_contract.py -k "shutdown or drain"` → 6 passed；含真实 PostgreSQL checkpoint 续跑、用户取消优先、宽限超时及无持久线程兜底。
- Task 1.3：隔离库中运行 Delta 修剪、v3 消息终态顺序及 Redis 分发三个定向用例 → 3 passed。

## 最终验证

- `DATABASE_URI=postgresql+asyncpg://lijiaxin@localhost:5432/graphharbor_event_retention_verify_drain_20261004` 与独立 Redis 前缀下，`uv run pytest -q "libs/langgraph-runtime-pg/tests"` → 162 passed，18 skipped（149.43s）。
- 另一专用库 `graphharbor_drain_20261004_test` 下，`uv run pytest -q "libs/langgraph-runtime-pg/tests/test_checkpoint_mutation_safety.py" "libs/langhost/tests"` → 74 passed，3 skipped（14.75s）。
- `uv run ruff check .` → 通过；五个改动 Python 文件 `ruff format --check` → 通过；三个改动源文件 `mypy` → 无问题；`git diff --check` → 通过。
- 首轮全套使用 `graphharbor_drain_20261004_test` 时出现 4 个失败：`test_event_retention.py` 要求库名包含 `graphharbor_event_retention_verify`，均在测试安全断言处停止。改用新建的、满足该门禁的独立库后全套通过。两次运行都未连接默认 `langgraph` 库。

## 最终结论

- 项目状态：`done`
- 四处状态一致性：README 为 `done`；tasks 三项完成卡均勾选；本文有三项 Phase 记录和独立 Final 验证；`docs/CONTEXT.md` 为 `done`。
- 剩余风险：停机宽限超时仍可能重跑未完成节点；原有 `retry_count` 上限仍适用于多次停机重排；回滚旧 Worker 前需先处理带内部续跑标记的 Run。
