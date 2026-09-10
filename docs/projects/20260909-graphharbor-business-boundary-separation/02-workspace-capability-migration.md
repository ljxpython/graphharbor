# 02 - Workspace 能力迁移

## 目标

将 `DeepAgentWorkspace` 从 `graphharbor-runtime` 核心包迁移到 `ai-agent-platform` 业务层，graphharbor 不再内建任何特定 Agent 框架的专用支持代码。

**为什么拆成独立一篇：** DeepAgent workspace 是特定业务框架（deepagents）的专用能力，与通用 LangGraph Agent Server 的职责边界明确不同，需要独立迁移和验证。

## 方案设计

### 当前问题

**graphharbor 硬编码的 DeepAgent 支持：**

1. **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/deepagent_workspace.py`**
   - `DeepAgentWorkspace` 数据类
   - `build_deepagent_workspace(base_dir, tenant_id, project_id, thread_id)` 构建器
   - `resolve_workspace_virtual_path()` 路径解析
   - `resolve_skill_sources()` skill 源文件解析

2. **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/__init__.py`**
   - `_MODULES` 列表包含 `"deepagent_workspace"`，默认导出

3. **依赖问题：**
   - `deepagent_workspace.py` import `deepagents.backends.filesystem.FilesystemBackend`
   - 但 `graphharbor-runtime` 的 `pyproject.toml` 并未声明 `deepagents` 依赖
   - 实际依赖藏在根目录 `uv.lock` 的 `[package.dev-dependencies.acceptance]` 中

**这导致：**
- graphharbor 核心包与特定业务框架（deepagents）耦合
- 其他用户安装 graphharbor 后，`from langgraph_runtime_pg import deepagent_workspace` 会因缺少 deepagents 依赖而失败
- 无法作为通用包发布

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ graphharbor (通用 Agent Server)                              │
│  - 不认得 DeepAgent                                          │
│  - 不认得 workspace                                          │
│  - 只负责 LangGraph graph 执行                               │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ graph factory 通过 configurable 传入
                            │
┌─────────────────────────────────────────────────────────────┐
│ ai-agent-platform/runtime-service (业务层)                   │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ runtime_service/workspace/deepagent.py               │  │
│  │                                                       │  │
│  │  - DeepAgentWorkspace 类                             │  │
│  │  - build_deepagent_workspace()                       │  │
│  │  - resolve_workspace_virtual_path()                  │  │
│  │  - resolve_skill_sources()                           │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ runtime_service/graphs/deep_agent_demo.py            │  │
│  │                                                       │  │
│  │  def deep_agent_demo_factory(config):                │  │
│  │      workspace = build_deepagent_workspace(...)      │  │
│  │      return create_deep_agent(workspace=workspace)   │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  pyproject.toml:                                            │
│    dependencies = ["deepagents==0.7.9", ...]               │
└─────────────────────────────────────────────────────────────┘
```

### 关键改动点

#### 1. graphharbor: 移除 deepagent_workspace 模块

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/deepagent_workspace.py`
- **改动：** 删除整个文件
- **理由：** 通用 Agent Server 不应内建特定框架支持

#### 2. graphharbor: 移除 __init__.py 导出

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/__init__.py`
- **改动：** `_MODULES` 列表移除 `"deepagent_workspace"`
- **理由：** 不再作为公开 API 导出

#### 3. graphharbor: 移除相关测试

- **文件：** `libs/langgraph-runtime-pg/tests/test_deepagent_workspace.py`
- **改动：** 删除整个测试文件
- **理由：** 功能已迁移到业务层

#### 4. ai-agent-platform: 新增 workspace 模块

- **文件：** `apps/runtime-service/src/runtime_service/workspace/__init__.py`
- **改动：** 创建新包目录

- **文件：** `apps/runtime-service/src/runtime_service/workspace/deepagent.py`
- **改动：** 从 graphharbor 复制 `deepagent_workspace.py` 全部内容，调整 import 路径

#### 5. ai-agent-platform: 更新 graph factory

- **文件：** `apps/runtime-service/src/runtime_service/graphs/deep_agent_demo.py`
- **改动：**
  ```python
  # 旧代码（依赖 graphharbor 内建）
  from langgraph_runtime_pg import deepagent_workspace
  
  # 新代码（使用本地业务层）
  from runtime_service.workspace.deepagent import build_deepagent_workspace
  
  def deep_agent_demo_factory(config: RunnableConfig):
      tenant_id = config["configurable"]["tenant_id"]
      project_id = config["configurable"]["project_id"]
      thread_id = config["configurable"]["thread_id"]
      
      workspace = build_deepagent_workspace(
          base_dir=Path("/data/workspaces"),  # 从环境变量读取
          tenant_id=tenant_id,
          project_id=project_id,
          thread_id=thread_id,
      )
      
      return create_deep_agent(workspace=workspace.backend, ...)
  ```

#### 6. ai-agent-platform: 声明 deepagents 依赖

- **文件：** `apps/runtime-service/pyproject.toml`
- **改动：**
  ```toml
  [project]
  dependencies = [
      "deepagents==0.7.9",
      ...
  ]
  ```
- **理由：** 业务层明确声明业务框架依赖

#### 7. ai-agent-platform: 更新其他引用点

- **文件：** `tests/acceptance_app/p0_graphs.py` （如果在 graphharbor 仓库）
- **改动：** 改为从 runtime-service 导入，或直接删除（acceptance 测试不应依赖具体业务 graph）

### 技术选型

- **模块路径：** `runtime_service.workspace.deepagent` —— 清晰表达这是业务层的 workspace 管理能力
- **依赖版本：** `deepagents==0.7.9` —— 锁定当前验收通过的版本
- **base_dir 配置：** 从环境变量 `DEEPAGENT_WORKSPACE_BASE_DIR` 读取，默认 `/data/workspaces`

## 任务拆分

### Phase 1: graphharbor 移除（2 个任务）

- [ ] Task 1.1：删除 deepagent_workspace.py 及其测试
  - **文件：**
    - `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/deepagent_workspace.py`
    - `libs/langgraph-runtime-pg/tests/test_deepagent_workspace.py`
  - **改动：** 删除这两个文件
  - **预计：** 0.25 天
  - **状态：** 待开始

- [ ] Task 1.2：移除 __init__.py 导出
  - **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/__init__.py`
  - **改动：** `_MODULES` 移除 `"deepagent_workspace"`
  - **预计：** 0.1 天
  - **状态：** 待开始

### Phase 2: ai-agent-platform 迁移（3 个任务）

- [ ] Task 2.1：创建 workspace 模块
  - **文件：** `apps/runtime-service/src/runtime_service/workspace/deepagent.py`
  - **改动：** 复制 graphharbor 的 deepagent_workspace.py 内容
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 2.2：更新 graph factory 引用
  - **文件：** `apps/runtime-service/src/runtime_service/graphs/deep_agent_demo.py`
  - **改动：** 改为 `from runtime_service.workspace.deepagent import build_deepagent_workspace`
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 2.3：声明 deepagents 依赖
  - **文件：** `apps/runtime-service/pyproject.toml`
  - **改动：** 在 `dependencies` 增加 `"deepagents==0.7.9"`
  - **预计：** 0.1 天
  - **状态：** 待开始

### Phase 3: 清理遗留引用（1 个任务）

- [ ] Task 3.1：清理 graphharbor 仓库的 acceptance 测试引用
  - **文件：** `tests/acceptance_app/p0_graphs.py` (如果存在)
  - **改动：** 移除对 deepagent_workspace 的 import，或整体迁移到 ai-agent-platform
  - **预计：** 0.5 天
  - **状态：** 待开始

## 验证要求与记录

### 验证要求

- [ ] graphharbor 单元测试：`pytest libs/langgraph-runtime-pg/tests/` 全部通过，无 deepagent 相关测试
- [ ] graphharbor 导入测试：`python -c "from langgraph_runtime_pg import deepagent_workspace"` 应抛出 `AttributeError`
- [ ] runtime-service 单元测试：新增 `tests/workspace/test_deepagent.py`，验证 workspace 构建和路径解析
- [ ] 集成测试：启动 graphharbor + runtime-service，执行 deep_agent_demo，验证 workspace 正常创建和隔离
- [ ] 回归测试：`apps/runtime-service/tests/test_deep_agent_demo.py` 通过

### 验证记录

#### 2026-09-09 验证
- 待执行

## 状态

规划中
