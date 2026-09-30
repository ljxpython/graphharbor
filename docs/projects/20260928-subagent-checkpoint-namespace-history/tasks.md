# 实施任务清单：子智能体命名空间状态与历史读取

- [x] Task 1：改造 `_checkpoint_config` 辅助函数以支持 `checkpoint_ns`；
  - 位置：`libs/langhost/src/langhost/core_api.py`；
  - 预期结果：当传入 `checkpoint_ns` 时，生成的 `configurable` 字典包含 `"checkpoint_ns": checkpoint_ns`，未传时保持默认；
  - 验证项：单元测试调用 `_checkpoint_config(thread_id, checkpoint_id, checkpoint_ns)` 断言返回结构；2026-09-28 已通过。

- [x] Task 2：改造 `threads_state` 接口支持提取并处理 `checkpoint_ns`；
  - 位置：`libs/langhost/src/langhost/core_api.py`；
  - 预期结果：
    1. 从 POST body `checkpoint.checkpoint_ns` 或 GET query `checkpoint_ns` 正确解析命名空间；
    2. 当 `checkpoint_ns` 非空时，绕过主图 `registry.open`，直接通过底层 `checkpointer.aget_tuple` 提取状态，避免 `Subgraph not found` 异常；
    3. 返回标准 `ThreadState`，其 `checkpoint.checkpoint_ns` 为所请求的命名空间。
  - 验证项：`uv run pytest libs/langhost/tests/test_subagent_history.py`，全部通过；2026-09-28 完成。

- [x] Task 3：改造 `threads_history` 接口支持提取并处理 `checkpoint_ns`；
  - 位置：`libs/langhost/src/langhost/core_api.py`；
  - 预期结果：
    1. 从 POST body `checkpoint.checkpoint_ns` 或 GET query `checkpoint_ns` 解析命名空间；
    2. 当 `checkpoint_ns` 非空时，直接使用 `checkpointer.alist(config, limit=...)` 查询子图时间线；
    3. 返回子图在指定命名空间下的历史快照列表；
  - 验证项：`uv run pytest libs/langhost/tests/test_subagent_history.py`，全部通过；2026-09-28 完成。

- [x] Task 4：编写完整单元与契约测试；
  - 位置：`libs/langhost/tests/test_subagent_history.py`；
  - 预期结果：模拟根图与子图 checkpoints（包含真实 tool_calls 与 tool 响应），覆盖根图查询、子图指定查询、子图历史列表、不存在的子命名空间 404/默认处理等分支；
  - 验证项：`uv run pytest -q libs/langhost/tests/test_subagent_history.py`，1 passed (100%)；2026-09-28 完成。

- [x] Task 5：发布准备与本地构建门禁检查；
  - 位置：`libs/langhost/pyproject.toml`、`libs/langgraph-runtime-pg/pyproject.toml`、`scripts/check_versions.py`；
  - 预期结果：两包版本号严格锁步，`uv lock --check` 与 `python3 scripts/check_versions.py` 检查通过，本地 `uv build` 构建无警告；
  - 验证项：执行 `python3 scripts/check_versions.py` 通过，`uv build` 成功生成 4 个轮子与源码分发包，并成功发布 `0.13.0.post37` 至 PyPI；2026-09-28 完成。
