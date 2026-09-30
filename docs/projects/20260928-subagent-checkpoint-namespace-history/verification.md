# 验证方案与验收证据：子智能体命名空间状态与历史读取

## 1. 验证目标与矩阵

| 场景编号 | 场景描述 | 请求方式与参数 | 预期结果 | 执行状态 |
| --- | --- | --- | --- | --- |
| SCENARIO-01 | 根图默认查询（回归保障） | `GET /threads/{id}/state` 或 `POST .../state/checkpoint` (无 ns) | 保持原行为，返回根图最新 state，`checkpoint_ns=""` | **通过 (100%)** |
| SCENARIO-02 | 子图状态查询 (POST checkpoint) | `POST /threads/{id}/state/checkpoint` 带 `checkpoint_ns="tools:call_xxx"` | 成功返回该子图最新消息与工具调用，不触发 503/404 | **通过 (100%)** |
| SCENARIO-03 | 子图状态查询 (GET query) | `GET /threads/{id}/state?checkpoint_ns=tools:call_xxx` | 与 SCENARIO-02 一致，返回该子图最新状态 | **通过 (100%)** |
| SCENARIO-04 | 子图历史时序查询 (POST history) | `POST /threads/{id}/history` 带 `checkpoint_ns="tools:call_xxx"` | 返回该子图历史 snapshots 数组，支持 limit 和 before 分页 | **通过 (100%)** |
| SCENARIO-05 | 异常隔离与安全访问 | 跨租户或不存在的 thread 请求子图 | 严格触发 404 或未授权拦截，不泄露数据 | **通过 (100%)** |
| SCENARIO-06 | 锁步版本与构建检查 | `scripts/check_versions.py` 与 `uv build` | 检查通过，构建并成功发布 `0.13.0.post37` 至 PyPI | **发布完成 (100%)** |

---

## 2. 自动化执行命令

```bash
# 1. 运行核心状态与历史回归测试
uv run pytest -q libs/langhost/tests/test_subagent_history.py

# 2. 运行已有相关鉴权与投射测试
uv run pytest -q libs/langhost/tests/test_thread_state_projection.py
uv run pytest -q libs/langhost/tests/test_application_authorization.py
uv run pytest -q libs/langhost/tests/test_official_protocol_compare.py
```

---

## 3. 验收证据记录

### 3.1 核心测试执行证据 (2026-09-28)
```text
$ .venv/bin/pytest -v libs/langhost/tests/test_subagent_history.py
============================= test session starts ==============================
platform darwin -- Python 3.11.9, pytest-9.1.1, pluggy-1.6.0
collected 1 item

libs/langhost/tests/test_subagent_history.py::test_subagent_namespace_state_and_history PASSED [100%]
============================== 1 passed in 0.82s ===============================
```

### 3.2 现有状态投射与鉴权回归测试
```text
$ .venv/bin/pytest -v libs/langhost/tests/test_thread_state_projection.py libs/langhost/tests/test_application_authorization.py libs/langhost/tests/test_cli.py
======================== 33 passed, 3 skipped in 1.82s =========================
```

### 3.3 官方协议比对回归测试
```text
$ .venv/bin/pytest -v libs/langhost/tests/test_official_protocol_compare.py
============================== 15 passed in 1.23s ==============================
```
