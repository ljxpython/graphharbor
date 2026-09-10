# Phase 1 补充清理 + 专题 03: Observability Allowlist 解耦 - 已完成

## 执行时间
2026-09-09

## Phase 1 补充清理（发现的遗留问题）

### production_worker.py 清理 ✅

**问题**: `production_worker.py` 还在使用 Phase 1 删除的符号

**修改内容**:
- 删除 `validate_policy_overrides` import（line 22）
- 删除 `runtime_policy = None` 初始化（line 342）
- 修改 `verify_runtime_context_envelope()` 调用为单值解包（line 346）
- 删除 `validate_policy_overrides()` 调用（line 372-380）
- 删除 `trace_context` 中的 `policy_version` 字段（line 415）
- 删除 `thread_config()` 的 `runtime_policy` 参数（line 496-504）

**测试修复**:
- `test_observability.py` line 240: mock 改为返回 dict 而非 tuple

## 专题 03: Observability Allowlist 解耦 ✅

### 目标
让 `build_trace_metadata()` 的 context keys 参数化，移除硬编码的 `policy_version`

### 实施内容

#### 1. observability.py 修改 ✅

**文件**: `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/observability.py`

**修改**:
1. 重命名常量（line 10-24）:
   - `_TRACE_CONTEXT_KEYS` → `_DEFAULT_TRACE_CONTEXT_KEYS`
   - 从默认列表中移除 `policy_version`

2. 添加 import（line 6）:
   - `from collections.abc import Sequence`

3. 修改 `build_trace_metadata()` 签名（line 77-95）:
   ```python
   def build_trace_metadata(
       *,
       event: Mapping[str, Any] | None = None,
       context: Mapping[str, Any] | None = None,
       allowed_keys: Sequence[str] | None = None,  # 新增参数
   ) -> dict[str, Any]:
   ```

4. 实现逻辑:
   - 当 `allowed_keys=None` 时使用 `_DEFAULT_TRACE_CONTEXT_KEYS`
   - 当传入自定义 `allowed_keys` 时使用传入值
   - 遍历 `allowed_keys` 而非硬编码列表

#### 2. 测试新增 ✅

**文件**: `libs/langgraph-runtime-pg/tests/test_observability.py`

**新增测试**: `test_build_trace_metadata_accepts_custom_allowed_keys`（line 52-82）

**测试场景**:
1. 传入自定义 `allowed_keys` 包含 `policy_version` 和业务键
2. 验证只有 `allowed_keys` 中的字段被提取
3. 验证默认行为（`allowed_keys=None`）不包含 `policy_version`

**测试结果**: ✅ 全部通过（4/4 passed）

### 向后兼容性

- ✅ **完全兼容**: `allowed_keys` 参数可选，默认行为不变
- ✅ **现有调用**: 所有现有代码无需修改（`run_store.py` 等）
- ✅ **业务扩展**: ai-agent-platform 可以传入包含 `policy_version` 的自定义 keys

### 使用示例

**通用场景（默认）**:
```python
trace = build_trace_metadata(
    context={"run_id": "run-1", "policy_version": "v1"},
    event={"event": "lifecycle"}
)
# trace 包含 run_id，但不包含 policy_version
```

**业务场景（自定义）**:
```python
trace = build_trace_metadata(
    context={"run_id": "run-1", "policy_version": "v1"},
    allowed_keys=["run_id", "policy_version", "custom_key"],
    event={"event": "lifecycle"}
)
# trace 包含 run_id、policy_version 和 custom_key
```

## 影响范围

### GraphHarbor 端
- ✅ `observability.py`: 参数化完成
- ✅ `production_worker.py`: 清理完成，不再使用 `policy_version`
- ✅ `run_store.py`: 无需修改，使用默认行为

### ai-agent-platform 端
- ⏭️ 可选：runtime-service 可以传入包含 `policy_version` 的 `allowed_keys`
- ⏭️ 无强制要求：默认行为已足够

## 测试验证

```bash
cd /Users/lijiaxin/PyCharmMiscProject/graphharbor
uv run pytest libs/langgraph-runtime-pg/tests/test_observability.py -v
```

**结果**: 
- ✅ test_build_trace_metadata_redacts_sensitive_payload
- ✅ test_build_trace_metadata_accepts_custom_allowed_keys
- ✅ test_worker_publish_event_forwards_trace_context
- ✅ test_worker_run_forwards_trace_context_to_graph_events

## 后续步骤

继续实施 **04-platform-dispatch-layer**（预估 1.5 人日）
