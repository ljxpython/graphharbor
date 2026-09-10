# 03 - Observability Allowlist 解耦

## 目标

将 observability 业务字段 allowlist（policy_version, model_id, tool_names 等）从 graphharbor 解耦，改为 runtime-service 配置管理，graphharbor 只提供通用 trace metadata 构建工具。

**为什么拆成独立一篇：** observability 字段过滤规则是具体业务的隐私/合规要求，不同团队的 allowlist 不同，graphharbor 不应硬编码一套具体规则。

## 方案设计

### 当前问题

**graphharbor 硬编码的业务字段：**

1. **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/observability.py`**
   - `_TRACE_CONTEXT_KEYS` 硬编码了业务字段列表：
     ```python
     _TRACE_CONTEXT_KEYS = (
         "run_id",
         "thread_id",
         "assistant_id",
         "assistant_version",
         "deployment_version",
         "tenant_id",
         "project_id",
         "user_id",
         "graph_id",
         "model_id",           # 业务字段
         "policy_version",     # 业务字段
         "request_id",
         "platform_trace_id",  # 业务字段
     )
     ```
   - `build_trace_metadata()` 将这些字段无条件写入 trace

2. **问题：**
   - `model_id`, `policy_version`, `platform_trace_id`, `tool_names` 是 platform-api 业务系统的具体字段
   - 其他团队使用 graphharbor 时，这些字段可能不存在，或有不同命名
   - 隐私合规要求不同团队的 allowlist 不同

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ graphharbor (通用 Agent Server)                              │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ observability.py                                      │  │
│  │                                                       │  │
│  │  _CORE_TRACE_KEYS = (                               │  │
│  │      "run_id", "thread_id", "assistant_id",         │  │
│  │      "tenant_id", "project_id", "user_id",          │  │
│  │  )                                                   │  │
│  │                                                       │  │
│  │  def build_trace_metadata(                           │  │
│  │      context: dict,                                  │  │
│  │      event: dict,                                    │  │
│  │      allowed_keys: tuple[str, ...] = _CORE_KEYS     │  │
│  │  ) -> dict:                                          │  │
│  │      # 只提取 allowed_keys 中的字段                  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ 业务层配置 allowed_keys
                            │
┌─────────────────────────────────────────────────────────────┐
│ ai-agent-platform/runtime-service (业务层)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ runtime_service/observability/config.py              │  │
│  │                                                       │  │
│  │  PLATFORM_TRACE_ALLOWED_KEYS = (                    │  │
│  │      *_CORE_TRACE_KEYS,  # 继承 graphharbor 核心字段 │  │
│  │      "model_id",                                     │  │
│  │      "policy_version",                               │  │
│  │      "request_id",                                   │  │
│  │      "platform_trace_id",                            │  │
│  │  )                                                   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ runtime_service/observability/langfuse.py            │  │
│  │                                                       │  │
│  │  def emit_trace(run_context: dict, event: dict):    │  │
│  │      metadata = build_trace_metadata(                │  │
│  │          context=run_context,                        │  │
│  │          event=event,                                │  │
│  │          allowed_keys=PLATFORM_TRACE_ALLOWED_KEYS   │  │
│  │      )                                               │  │
│  │      langfuse_client.trace(..., metadata=metadata)  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 关键改动点

#### 1. graphharbor: 改为参数化 allowed_keys

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/observability.py`
- **改动：**
  ```python
  # 只保留通用核心字段
  _CORE_TRACE_KEYS = (
      "run_id",
      "thread_id",
      "assistant_id",
      "assistant_version",
      "deployment_version",
      "tenant_id",
      "project_id",
      "user_id",
      "graph_id",
  )
  
  _SUMMARY_KEYS = (
      "data", "input", "output", "error", "interrupts",
      "content", "prompt", "response"
  )
  
  def build_trace_metadata(
      *,
      event: Mapping[str, Any] | None = None,
      context: Mapping[str, Any] | None = None,
      allowed_keys: tuple[str, ...] = _CORE_TRACE_KEYS,  # 新增参数
  ) -> dict[str, Any]:
      trace: dict[str, Any] = {"schema_version": 1}
      if context:
          for key in allowed_keys:  # 改为使用参数
              value = context.get(key)
              if value is not None and value != "":
                  trace[key] = str(value)
          # tool_names 也改为可选
          if "tool_names" in allowed_keys:
              tool_names = context.get("tool_names")
              if tool_names:
                  trace["tool_names"] = summarize_value(tool_names)
      # event 处理保持不变
      ...
      return trace
  ```
- **理由：** 让调用方（业务层）决定哪些字段进入 trace

#### 2. graphharbor: 移除硬编码的业务字段

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/observability.py`
- **改动：**
  - `_TRACE_CONTEXT_KEYS` 重命名为 `_CORE_TRACE_KEYS`
  - 移除 `"model_id"`, `"policy_version"`, `"request_id"`, `"platform_trace_id"` 等业务字段
- **理由：** 这些是 platform-api 业务系统的具体字段，不是通用字段

#### 3. graphharbor: 更新单元测试

- **文件：** `libs/langgraph-runtime-pg/tests/test_observability.py`
- **改动：**
  - 测试 `build_trace_metadata()` 默认只提取核心字段
  - 新增测试：传入 `allowed_keys=(...)` 参数，验证自定义字段可提取
  - 验证业务字段在默认情况下**不进入** trace
- **理由：** 确保通用性和参数化工作正常

#### 4. ai-agent-platform: 配置业务 allowlist

- **文件：** `apps/runtime-service/src/runtime_service/observability/config.py`
- **改动：**
  ```python
  """Platform-specific observability configuration."""
  
  from langgraph_runtime_pg.observability import _CORE_TRACE_KEYS
  
  # Platform-API 业务字段 allowlist
  PLATFORM_TRACE_ALLOWED_KEYS = (
      *_CORE_TRACE_KEYS,
      "model_id",
      "policy_version",
      "request_id",
      "platform_trace_id",
      "tool_names",  # 显式声明
  )
  
  # 也可以通过环境变量覆盖
  # OBSERVABILITY_ALLOWED_KEYS=run_id,thread_id,model_id,...
  ```
- **理由：** 业务层明确声明自己需要哪些字段

#### 5. ai-agent-platform: 更新 langfuse 集成

- **文件：** `apps/runtime-service/src/runtime_service/observability/langfuse.py`
- **改动：**
  ```python
  from langgraph_runtime_pg.observability import build_trace_metadata
  from runtime_service.observability.config import PLATFORM_TRACE_ALLOWED_KEYS
  
  def emit_trace_event(run_context: dict, event: dict):
      metadata = build_trace_metadata(
          context=run_context,
          event=event,
          allowed_keys=PLATFORM_TRACE_ALLOWED_KEYS,  # 传入业务 allowlist
      )
      langfuse_client.trace(
          id=run_context["run_id"],
          metadata=metadata,
          ...
      )
  ```
- **理由：** 业务层调用时显式传入自己的 allowlist

#### 6. ai-agent-platform: 更新 OTLP 集成（如果有）

- **文件：** `apps/runtime-service/src/runtime_service/observability/otel.py`
- **改动：** 同 langfuse，传入 `allowed_keys=PLATFORM_TRACE_ALLOWED_KEYS`
- **理由：** 保持一致性

### 技术选型

- **配置方式：** 优先使用 Python 常量 `PLATFORM_TRACE_ALLOWED_KEYS`，可选支持环境变量覆盖
- **向后兼容：** graphharbor 的 `build_trace_metadata()` 默认参数保持核心字段，不破坏现有调用
- **扩展性：** 业务层可以在 allowlist 中增加自己的自定义字段（如 `"experiment_id"`, `"feature_flag"`）

## 任务拆分

### Phase 1: graphharbor 参数化（2 个任务）

- [ ] Task 1.1：build_trace_metadata 增加 allowed_keys 参数
  - **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/observability.py`
  - **函数：** `build_trace_metadata()`
  - **改动：** 增加 `allowed_keys: tuple[str, ...] = _CORE_TRACE_KEYS` 参数，循环改为使用该参数
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 1.2：更新单元测试
  - **文件：** `libs/langgraph-runtime-pg/tests/test_observability.py`
  - **改动：** 新增测试用例验证参数化和默认值
  - **预计：** 0.5 天
  - **状态：** 待开始

### Phase 2: ai-agent-platform 业务配置（3 个任务）

- [ ] Task 2.1：创建 observability config 模块
  - **文件：** `apps/runtime-service/src/runtime_service/observability/config.py`
  - **改动：** 定义 `PLATFORM_TRACE_ALLOWED_KEYS`
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 2.2：更新 langfuse 集成
  - **文件：** `apps/runtime-service/src/runtime_service/observability/langfuse.py`
  - **改动：** 传入 `allowed_keys=PLATFORM_TRACE_ALLOWED_KEYS`
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 2.3：更新 OTLP 集成（如果有）
  - **文件：** `apps/runtime-service/src/runtime_service/observability/otel.py`
  - **改动：** 传入 `allowed_keys=PLATFORM_TRACE_ALLOWED_KEYS`
  - **预计：** 0.5 天
  - **状态：** 待开始

## 验证要求与记录

### 验证要求

- [ ] graphharbor 单元测试：`test_build_trace_metadata_default_keys()` 验证默认只提取核心字段
- [ ] graphharbor 单元测试：`test_build_trace_metadata_custom_keys()` 验证可传入自定义 allowed_keys
- [ ] runtime-service 集成测试：发起 run，验证 Langfuse trace metadata 包含 `model_id`, `policy_version` 等业务字段
- [ ] runtime-service 集成测试：验证 OTLP span attributes 包含正确的业务字段
- [ ] 回归测试：现有 showcase-demo 等 graph 的 observability 仍正常工作

### 验证记录

#### 2026-09-09 验证
- 待执行

## 状态

规划中
