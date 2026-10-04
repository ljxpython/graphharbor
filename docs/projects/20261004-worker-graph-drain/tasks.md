# Worker 图级优雅停机任务

### Task 1.1: 传递 LangGraph drain 控制
- **改动内容：** 执行适配器接受 `RunControl` 并传入图调用。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/graph_executor.py` → `invoke_graph()`
- **预期结果：** 请求 drain 后图在 superstep 边界抛 `GraphDrained`，检查点可恢复。
- **验证项：** `uv run pytest -q "libs/langgraph-runtime-pg/tests/test_public_runtime.py"` → 18 passed
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：**
  - [x] 代码实现完成且通过 Ruff 代码与格式检查
  - [x] 针对性 Phase 验证已真实执行
  - [x] tasks.md 状态已勾选更新
  - [x] docs/CONTEXT.md 已同步当前项目状态
  - [x] docs/FEATURES.md 已同步功能状态
  - [x] docs/CHANGELOG.md 已在 [Unreleased] 记录能力变动

### Task 1.2: Worker 停机、重排和续跑
- **改动内容：** 区分停机与主动取消；安全 drain 后标记空输入续跑，并保留宽限期兜底。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` → `ProductionWorker.run_once()`；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py` → `requeue_for_shutdown()`
- **预期结果：** 已完成节点不重复运行；用户取消、运行超时与崩溃回收保持原语义。
- **验证项：** 隔离数据库中运行 `DATABASE_URI=... uv run pytest -q "libs/langgraph-runtime-pg/tests/test_production_contract.py" -k "shutdown or drain"` → 6 passed
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：**
  - [x] 代码实现完成且通过 Ruff 代码与格式检查
  - [x] 针对性 Phase 验证已真实执行
  - [x] tasks.md 状态已勾选更新
  - [x] docs/CONTEXT.md 已同步当前项目状态
  - [x] docs/FEATURES.md 已同步功能状态
  - [x] docs/CHANGELOG.md 已在 [Unreleased] 记录能力变动

### Task 1.3: 状态与事件回归
- **改动内容：** 覆盖同 Run 重排、SSE 生命周期投影和 Delta checkpoint 兼容；同步状态文档。
- **代码位置：** `libs/langgraph-runtime-pg/tests/test_production_contract.py`；`libs/langhost/tests/`；`docs/`
- **预期结果：** pending/shutdown_requeue 与续跑终态顺序一致，无新公开事件。
- **验证项：** 隔离数据库中运行 `test_delta_prune_and_subgraph_history`、v3 消息顺序和 Redis 分发用例 → 3 passed；完整回归见 `verification.md`
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：**
  - [x] 测试与文档实现完成且通过 Ruff 代码与格式检查
  - [x] 针对性 Phase 验证已真实执行
  - [x] tasks.md 状态已勾选更新
  - [x] docs/CONTEXT.md 已同步当前项目状态
  - [x] docs/FEATURES.md 已同步功能状态
  - [x] docs/CHANGELOG.md 已在 [Unreleased] 记录能力变动
