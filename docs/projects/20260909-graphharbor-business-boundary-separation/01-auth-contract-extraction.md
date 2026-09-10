# 01 - 鉴权契约提取

## 目标

将硬编码在 `graphharbor-runtime` 的 platform-api delegation JWT 校验逻辑提取为可插拔契约，通过 langgraph.json `auth` handler 由 runtime-service 提供，graphharbor 只保留通用 Principal 抽象和 ASGI middleware 骨架。

**为什么拆成独立一篇：** 鉴权是最核心的业务耦合点，涉及 JWT 格式、policy claims、tenant/project 隔离等具体业务规则，必须首先解耦才能让 graphharbor 成为真正通用的 Agent Server。

## 方案设计

### 当前问题

**graphharbor 硬编码的业务逻辑：**

1. **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`**
   - `RuntimePolicy(allowed_model_ids, allowed_tool_names)` 数据结构
   - `DelegationJWTValidator` 类（JWKS/shared secret 校验）
   - `sign_runtime_context()` / `verify_runtime_context()` 生成签名 context token
   - `Principal.from_claims()` 从 delegation JWT claims 构建 Principal

2. **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/protocol.py`**
   - `capability_document()` 返回 `"authentication": {"production": "platform-api-delegation-jwt"}`
   - 硬编码认得 "platform-api" 这个具体业务系统名称

3. **`libs/langhost/src/langhost/core_api.py`**
   - 路由层直接调用 `DelegationJWTValidator` 校验请求

**这导致：**
- 其他团队无法用 graphharbor，除非他们也实现一模一样的 delegation JWT 格式
- graphharbor 无法作为通用包发布到 PyPI（依赖具体业务鉴权规则）

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ graphharbor (通用 Agent Server)                              │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ PrincipalMiddleware (ASGI)                           │  │
│  │  - 调用 langgraph.json 配置的 auth handler           │  │
│  │  - 将返回的 user 对象转为 Principal                  │  │
│  │  - 放入 scope["principal"]                           │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ Principal (通用抽象)                                  │  │
│  │  - subject, tenant_id, project_id                    │  │
│  │  - roles, scopes                                     │  │
│  │  - generic claims: dict[str, Any]                   │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ langgraph.json "auth"
                            │
┌─────────────────────────────────────────────────────────────┐
│ ai-agent-platform/runtime-service (业务适配层)               │
│                                                              │
│  langgraph.json:                                            │
│    "auth": "runtime_service.auth.platform:authenticate"     │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ runtime_service/auth/platform.py                     │  │
│  │                                                       │  │
│  │  async def authenticate(                             │  │
│  │      authorization: str,                             │  │
│  │      path: str,                                      │  │
│  │      ...                                             │  │
│  │  ) -> dict[str, Any]:                               │  │
│  │      # 校验 platform-api delegation JWT              │  │
│  │      # 返回 user dict (identity, tenant_id, ...)    │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 关键改动点

#### 1. graphharbor: 保留通用 Principal 抽象

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`
- **改动：**
  - 保留 `Principal` 类（subject, tenant_id, project_id, roles, scopes, claims）
  - 保留 `Principal.from_auth_user(user: Any)` —— 从 auth handler 返回的 user 对象构建 Principal
  - **删除** `DelegationJWTValidator` 类
  - **删除** `RuntimePolicy` 数据结构
  - **删除** `sign_runtime_context()` / `verify_runtime_context()` 及其 policy 参数
  - **删除** `_RUNTIME_CONTEXT_FIELDS` 中的 policy 相关字段
- **理由：** graphharbor 不应认得具体的 JWT 格式和 policy 结构，只需要一个通用的 Principal 抽象接收 auth handler 的结果

#### 2. graphharbor: PrincipalMiddleware 调用 auth handler

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`
- **改动：**
  - `PrincipalMiddleware.__init__()` 移除 `validator: DelegationJWTValidator | None` 参数
  - 改为 `auth_handler: Any | None` 参数（从 langgraph.json 加载）
  - `__call__()` 方法：
    - 健康端点（/ok /ready /info /openapi.json /metrics）跳过鉴权
    - 其他路径：调用 `authenticate_with_auth_handler(auth_handler, scope, receive, authorization)`
    - 将返回的 user 对象转为 `Principal.from_auth_user(user)`
    - 放入 `scope["principal"]`
- **理由：** 遵循 LangGraph 官方 custom auth 机制，不自造鉴权协议

#### 3. graphharbor: 移除 protocol.py 的业务命名

- **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/protocol.py`
- **改动：**
  - `capability_document()` 返回值中移除 `"authentication"` 字段
  - 或改为通用描述：`"authentication": {"type": "custom_auth_handler"}`
- **理由：** 不应在能力声明中硬编码 "platform-api-delegation-jwt" 这种具体业务系统名称

#### 4. ai-agent-platform: 实现 delegation JWT auth handler

- **文件：** `apps/runtime-service/src/runtime_service/auth/platform.py`
- **改动：**
  - 新增 `async def authenticate(authorization: str | None, path: str, headers: dict, ...) -> dict[str, Any]`
  - 从 graphharbor 迁移 `DelegationJWTValidator` 逻辑到这里
  - 从 graphharbor 迁移 `RuntimePolicy` 数据结构到 `runtime_service/runtime/contracts.py`
  - 返回 user dict：
    ```python
    {
        "identity": principal.subject,
        "tenant_id": principal.tenant_id,
        "project_id": principal.project_id,
        "role": list(principal.roles)[0] if principal.roles else "user",
        "permissions": list(principal.scopes),
        "policy_version": policy.version,
        "allowed_model_ids": list(policy.allowed_model_ids),
        "allowed_tool_names": list(policy.allowed_tool_names),
    }
    ```
- **理由：** 业务鉴权逻辑归属业务层，通过 langgraph.json auth handler 注入

#### 5. ai-agent-platform: 配置 langgraph.json auth

- **文件：** `apps/runtime-service/langgraph.json`
- **改动：**
  ```json
  {
    "graphs": { ... },
    "dependencies": ["."],
    "http": {
      "app": "runtime_service.webapp:app"
    },
    "auth": {
      "path": "runtime_service.auth.platform:authenticate"
    },
    "env": ".env"
  }
  ```
- **理由：** 官方 langgraph.json 标准配置

#### 6. ai-agent-platform: RuntimeContext 签名迁移

- **当前问题：** graphharbor 的 `sign_runtime_context()` 承载了 policy 签名逻辑，worker 执行时需要从签名 token 恢复 policy
- **改动方案：**
  - **选项 A（推荐）：** runtime-service graph factory 从 auth handler 返回的 `configurable.langgraph_auth_user` 获取 policy，不再依赖 graphharbor 的签名 context token
  - **选项 B：** 在 runtime-service 自己实现 `sign_runtime_context()` / `verify_runtime_context()`，graphharbor 只负责传递
- **文件：** `apps/runtime-service/src/runtime_service/runtime/auth.py`
- **理由：** policy 是业务概念，签名/校验逻辑应在业务层

### 技术选型

- **auth handler 协议：** 遵循 langgraph.json 官方 `auth.path` 配置，签名为 `async def authenticate(...) -> Any`
- **Principal 构建：** 使用 graphharbor 现有的 `Principal.from_auth_user(user)` 方法，不改接口
- **JWT 校验库：** 继续使用 PyJWT，但依赖声明在 runtime-service 的 pyproject.toml
- **环境变量：** `GRAPHHARBOR_JWT_*` 环境变量保留，但由 runtime-service auth handler 读取

## 任务拆分

### Phase 1: graphharbor 解耦（3 个任务）

- [x] Task 1.1：从 auth.py 移除 DelegationJWTValidator 和 RuntimePolicy
  - **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`
  - **改动：** 删除 `DelegationJWTValidator` 类、`RuntimePolicy` 类、`sign_runtime_context()` 中的 policy 参数
  - **预计：** 0.5 天
  - **状态：** 待开始

- [x] Task 1.2：PrincipalMiddleware 改为调用 auth_handler
  - **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`
  - **函数/类：** `PrincipalMiddleware.__init__()`, `PrincipalMiddleware.__call__()`
  - **改动：** 移除 validator 参数，改为 auth_handler；调用 `authenticate_with_auth_handler()`
  - **预计：** 0.5 天
  - **状态：** 待开始

- [x] Task 1.3：移除 protocol.py 的业务命名
  - **文件：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/protocol.py`
  - **改动：** `capability_document()` 移除 `"authentication": {"production": "platform-api-delegation-jwt"}`
  - **预计：** 0.25 天
  - **状态：** 待开始

### Phase 2: ai-agent-platform 业务层实现（3 个任务）

- [ ] Task 2.1：迁移 DelegationJWTValidator 到 runtime-service
  - **文件：** `apps/runtime-service/src/runtime_service/auth/platform.py`
  - **改动：** 从 graphharbor auth.py 复制 `DelegationJWTValidator` 类并调整导入
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 2.2：实现 authenticate() auth handler
  - **文件：** `apps/runtime-service/src/runtime_service/auth/platform.py`
  - **函数：** `async def authenticate(authorization: str | None, ...) -> dict[str, Any]`
  - **改动：** 调用 DelegationJWTValidator 校验 JWT，返回 user dict（含 policy）
  - **预计：** 1 天
  - **状态：** 待开始

- [ ] Task 2.3：配置 langgraph.json auth
  - **文件：** `apps/runtime-service/langgraph.json`
  - **改动：** 新增 `"auth": {"path": "runtime_service.auth.platform:authenticate"}`
  - **预计：** 0.25 天
  - **状态：** 待开始

### Phase 3: RuntimeContext 签名迁移（2 个任务）

- [ ] Task 3.1：runtime-service 自己实现 sign/verify context（如果选择选项 B）
  - **文件：** `apps/runtime-service/src/runtime_service/runtime/auth.py`
  - **改动：** 实现带 policy 的 sign_runtime_context / verify_runtime_context
  - **预计：** 1 天
  - **状态：** 待开始

- [ ] Task 3.2：graph factory 从 configurable 获取 policy（如果选择选项 A）
  - **文件：** `apps/runtime-service/src/runtime_service/graphs/*.py`
  - **改动：** 从 `config["configurable"]["langgraph_auth_user"]` 读取 policy
  - **预计：** 0.5 天
  - **状态：** 待开始

## 验证要求与记录

### 验证要求

- [ ] graphharbor 单元测试：`libs/langgraph-runtime-pg/tests/test_auth.py` 移除 delegation JWT 相关测试
- [ ] graphharbor 单元测试：保留 `Principal.from_auth_user()` 测试，验证通用 user dict 转换
- [ ] runtime-service 单元测试：新增 `tests/test_auth_platform.py`，验证 delegation JWT 校验逻辑
- [ ] 集成测试：platform-api 发起带 delegation JWT 的请求到 graphharbor，验证 Principal 正确注入
- [ ] 集成测试：验证 graph factory 能从 `configurable.langgraph_auth_user` 获取 policy 并校验 model_id/tool_names
- [ ] 回归测试：现有 showcase-demo、deep-agent-demo 等业务 graph 仍能正常鉴权和执行

### 验证记录

#### 2026-09-09 验证
- 待执行

## 状态

规划中
