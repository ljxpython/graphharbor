# 04 - Platform Dispatch 层实现

## 目标

借鉴 open-swe 的 `dispatch.py` 设计，在 ai-agent-platform/platform-api 实现统一 dispatch 层，封装 graphharbor 标准协议调用，替代直接调用 `langgraph_sdk.get_client().runs.create()`，不新增业务端点到 graphharbor。

**为什么拆成独立一篇：** 这是上层业务系统的调用封装层，与前三个专题（graphharbor 解耦）是独立的实现模块，且参考了 open-swe 的成熟设计模式。

## 方案设计

### 当前问题

**platform-api 直接调用 langgraph SDK：**

1. **分散的调用点：**
   - platform-web → platform-api → runtime-service
   - 多处代码直接 `get_client().runs.create(...)`
   - 每处调用都要重复构建 `input`, `stream_mode`, `webhook`, `multitask_strategy` 等参数

2. **缺少统一配置：**
   - Protocol v2 stream mode 配置分散
   - `durability="sync"` 等生产最佳实践没有统一执行
   - webhook URL 拼接逻辑重复

3. **业务逻辑混入调用方：**
   - delegation JWT 构建分散在 platform-api 各处
   - tenant/project/user 上下文传递不一致

### open-swe dispatch.py 的核心设计

**参考文件：** `/Users/lijiaxin/PyCharmMiscProject/research/open-swe/agent/dispatch.py`

**核心价值：**

1. **统一 run 配置：**
   ```python
   V2_RUN_STREAM_MODES = ("values", "updates", "messages", "custom", "tasks", "checkpoints")
   EVENT_STREAMING_V2_CONFIG_KEY = "__event_streaming_v2"
   
   async def dispatch_run(
       assistant_id: str,
       content: str | list[dict],
       source: str,  # slack/linear/github/web/desktop
       configurable: dict,
       *,
       multitask_strategy: str = "interrupt",  # 默认 interrupt
       stream_subgraphs: bool = True,
   ) -> Run:
       client = get_client(url=LANGGRAPH_API_URL)
       return await client.runs.create(
           thread_id=thread_id,
           assistant_id=assistant_id,
           input=build_run_input(content, source, configurable),
           stream_mode=V2_RUN_STREAM_MODES,
           stream_subgraphs=stream_subgraphs,
           config={
               "configurable": {
                   **configurable,
                   EVENT_STREAMING_V2_CONFIG_KEY: True,  # 开启 Protocol v2
               }
           },
           multitask_strategy=multitask_strategy,
           webhook=COMPLETION_WEBHOOK_URL,
       )
   ```

2. **统一 input 构建：**
   ```python
   def build_run_input(
       content: str | list[dict],
       source: str,
       configurable: dict,
   ) -> RunInput:
       # 标准化消息格式
       # 添加 metadata（source, surface, people, channels）
       # 构建 thread context
   ```

3. **统一 webhook 回调：**
   - 所有 run 完成/失败都回调 platform-api
   - 不用客户端轮询 run status

### 目标架构

```
┌─────────────────────────────────────────────────────────────┐
│ platform-web (前端)                                          │
│  - 用户操作触发 run                                          │
│  - 调用 platform-api `/api/v1/agents/{agent_id}/runs`      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ platform-api (网关 + 业务编排)                               │
│                                                              │
│  POST /api/v1/agents/{agent_id}/runs                       │
│  ├─ 鉴权：验证 platform user session                        │
│  ├─ 构建 delegation JWT                                     │
│  └─ 调用 dispatch_agent_run()                               │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ platform_api/runtime/dispatch.py                     │  │
│  │                                                       │  │
│  │  async def dispatch_agent_run(                       │  │
│  │      agent_id: str,                                  │  │
│  │      user_message: str,                              │  │
│  │      platform_user: User,                            │  │
│  │      thread_id: str | None = None,                  │  │
│  │  ) -> Run:                                           │  │
│  │      # 1. 生成 delegation JWT                        │  │
│  │      jwt_token = build_delegation_jwt(platform_user) │  │
│  │                                                       │  │
│  │      # 2. 调用 graphharbor 标准协议                  │  │
│  │      client = get_client(                            │  │
│  │          url=GRAPHHARBOR_API_URL,                    │  │
│  │          headers={"Authorization": f"Bearer {jwt}"}  │  │
│  │      )                                               │  │
│  │      return await client.runs.create(                │  │
│  │          thread_id=thread_id,                        │  │
│  │          assistant_id=agent_id,                      │  │
│  │          input={"messages": [...]},                  │  │
│  │          stream_mode=V2_RUN_STREAM_MODES,            │  │
│  │          multitask_strategy="interrupt",             │  │
│  │          webhook=build_webhook_url(),                │  │
│  │          config={                                     │  │
│  │              "configurable": {                       │  │
│  │                  EVENT_STREAMING_V2_KEY: True        │  │
│  │              }                                       │  │
│  │          },                                           │  │
│  │      )                                               │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼ LangGraph SDK 标准调用
┌─────────────────────────────────────────────────────────────┐
│ graphharbor (通用 Agent Server)                              │
│  - 验证 delegation JWT (通过 auth handler)                  │
│  - 执行 graph                                               │
│  - SSE 流式返回                                             │
│  - webhook 回调                                             │
└─────────────────────────────────────────────────────────────┘
```

### 关键改动点

#### 1. platform-api: 创建 dispatch 模块

- **文件：** `apps/platform-api/app/runtime/dispatch.py`
- **改动：**
  ```python
  """Unified GraphHarbor dispatch layer.
  
  Replaces scattered runs.create() calls with a single dispatch contract
  that applies production best practices (v2 streaming, sync durability,
  webhook, interrupt strategy) by default.
  """
  
  import os
  from typing import Any
  from langgraph_sdk import get_client
  from langgraph_sdk.schema import Run
  
  # Protocol v2 标准 stream modes
  V2_RUN_STREAM_MODES = (
      "values", "updates", "messages", "custom", "tasks", "checkpoints"
  )
  
  EVENT_STREAMING_V2_CONFIG_KEY = "__event_streaming_v2"
  
  GRAPHHARBOR_API_URL = os.getenv(
      "GRAPHHARBOR_API_URL",
      "http://127.0.0.1:31296"
  )
  
  COMPLETION_WEBHOOK_URL = os.getenv(
      "COMPLETION_WEBHOOK_URL",
      "http://platform-api:8000/api/v1/runtime/webhooks/completion"
  )
  
  
  async def dispatch_agent_run(
      *,
      agent_id: str,
      user_message: str,
      delegation_jwt: str,
      thread_id: str | None = None,
      multitask_strategy: str = "interrupt",
      source: str = "web",
      metadata: dict[str, Any] | None = None,
  ) -> Run:
      """Dispatch a run to GraphHarbor with standard production config.
      
      Args:
          agent_id: Assistant ID from langgraph.json
          user_message: User's input message
          delegation_jwt: Platform-API delegation token
          thread_id: Optional thread to continue
          multitask_strategy: "interrupt" (default) or "enqueue"
          source: "web", "slack", "linear", etc.
          metadata: Optional run metadata
      
      Returns:
          Run object with run_id, thread_id, status
      """
      client = get_client(
          url=GRAPHHARBOR_API_URL,
          headers={"Authorization": f"Bearer {delegation_jwt}"},
      )
      
      run_input = {
          "messages": [
              {
                  "role": "user",
                  "content": user_message,
              }
          ],
          "source": source,
      }
      if metadata:
          run_input["metadata"] = metadata
      
      return await client.runs.create(
          thread_id=thread_id,
          assistant_id=agent_id,
          input=run_input,
          stream_mode=V2_RUN_STREAM_MODES,
          stream_subgraphs=True,
          multitask_strategy=multitask_strategy,
          webhook=COMPLETION_WEBHOOK_URL,
          config={
              "configurable": {
                  EVENT_STREAMING_V2_CONFIG_KEY: True,
              }
          },
      )
  
  
  async def dispatch_agent_stream(
      *,
      agent_id: str,
      user_message: str,
      delegation_jwt: str,
      thread_id: str | None = None,
      multitask_strategy: str = "interrupt",
  ):
      """Stream run events from GraphHarbor.
      
      Yields SSE events directly from langgraph_sdk.
      """
      client = get_client(
          url=GRAPHHARBOR_API_URL,
          headers={"Authorization": f"Bearer {delegation_jwt}"},
      )
      
      async for chunk in client.runs.stream(
          thread_id=thread_id,
          assistant_id=agent_id,
          input={"messages": [{"role": "user", "content": user_message}]},
          stream_mode=V2_RUN_STREAM_MODES,
          stream_subgraphs=True,
          multitask_strategy=multitask_strategy,
          config={
              "configurable": {
                  EVENT_STREAMING_V2_CONFIG_KEY: True,
              }
          },
      ):
          yield chunk
  ```

#### 2. platform-api: 更新 agent run 路由

- **文件：** `apps/platform-api/app/api/v1/agents.py`
- **改动：**
  ```python
  from app.runtime.dispatch import dispatch_agent_run, dispatch_agent_stream
  from app.auth.jwt import build_delegation_jwt
  
  @router.post("/agents/{agent_id}/runs")
  async def create_agent_run(
      agent_id: str,
      request: CreateRunRequest,
      current_user: User = Depends(get_current_user),
  ) -> RunResponse:
      # 1. 生成 delegation JWT
      delegation_jwt = build_delegation_jwt(
          user_id=current_user.id,
          tenant_id=current_user.tenant_id,
          project_id=request.project_id or current_user.default_project_id,
          policy=get_user_runtime_policy(current_user),
      )
      
      # 2. 调用统一 dispatch
      run = await dispatch_agent_run(
          agent_id=agent_id,
          user_message=request.message,
          delegation_jwt=delegation_jwt,
          thread_id=request.thread_id,
          source="web",
          metadata={"user_id": current_user.id},
      )
      
      # 3. 记录 platform-api 自己的 run 数据
      await save_run_record(run.run_id, current_user.id, agent_id)
      
      return RunResponse(
          run_id=run.run_id,
          thread_id=run.thread_id,
          status=run.status,
      )
  
  
  @router.get("/agents/{agent_id}/runs/{run_id}/stream")
  async def stream_agent_run(
      agent_id: str,
      run_id: str,
      current_user: User = Depends(get_current_user),
  ):
      delegation_jwt = build_delegation_jwt(current_user)
      
      return StreamingResponse(
          dispatch_agent_stream(
              agent_id=agent_id,
              user_message="",  # 空，因为 run 已存在
              delegation_jwt=delegation_jwt,
              thread_id=run_id,  # 错误：应该从 run 查 thread_id
          ),
          media_type="text/event-stream",
      )
  ```

#### 3. platform-api: 实现 delegation JWT builder

- **文件：** `apps/platform-api/app/auth/jwt.py`
- **改动：**
  ```python
  from runtime_service.runtime.contracts import RuntimePolicy
  
  def build_delegation_jwt(
      user_id: str,
      tenant_id: str,
      project_id: str,
      policy: RuntimePolicy,
  ) -> str:
      """Build platform-api delegation JWT for runtime-service.
      
      Claims:
          - sub: user_id
          - tenant_id, project_id
          - type: "runtime_delegation"
          - allowed_model_ids, allowed_tool_names (policy)
      """
      claims = {
          "sub": user_id,
          "tenant_id": tenant_id,
          "project_id": project_id,
          "type": "runtime_delegation",
          "allowed_model_ids": list(policy.allowed_model_ids),
          "allowed_tool_names": list(policy.allowed_tool_names),
          "policy_version": policy.version,
          "iat": int(time.time()),
          "exp": int(time.time()) + 300,  # 5 分钟
      }
      return jwt.encode(claims, JWT_SECRET, algorithm="HS256")
  ```

#### 4. platform-api: 实现 webhook 回调接收

- **文件：** `apps/platform-api/app/api/v1/runtime/webhooks.py`
- **改动：**
  ```python
  @router.post("/webhooks/completion")
  async def handle_run_completion(webhook: WebhookPayload):
      """Receive run completion/failure webhook from GraphHarbor.
      
      Update platform-api's run record, notify user, etc.
      """
      run_id = webhook.run_id
      status = webhook.status  # "success" | "error" | "interrupted"
      
      await update_run_status(run_id, status)
      
      if status == "success":
          # 通知用户 run 完成
          await notify_user_run_complete(run_id)
      elif status == "error":
          # 记录错误日志
          await log_run_error(run_id, webhook.error)
      
      return {"ok": True}
  ```

#### 5. 删除分散的直接调用点

- **文件：** `apps/platform-api/app/services/agent_service.py` 等
- **改动：** 将所有 `get_client().runs.create()` 调用替换为 `dispatch_agent_run()`

### 技术选型

- **dispatch 模块位置：** `app/runtime/dispatch.py` —— 表达这是 runtime 编排层
- **delegation JWT builder：** `app/auth/jwt.py` —— 鉴权相关逻辑集中管理
- **webhook 回调：** `app/api/v1/runtime/webhooks.py` —— 独立路由接收 graphharbor 回调
- **环境变量：**
  - `GRAPHHARBOR_API_URL`: graphharbor 服务地址
  - `COMPLETION_WEBHOOK_URL`: platform-api 回调地址

## 任务拆分

### Phase 1: dispatch 层基础（3 个任务）

- [ ] Task 1.1：创建 dispatch.py 模块
  - **文件：** `apps/platform-api/app/runtime/dispatch.py`
  - **改动：** 实现 `dispatch_agent_run()` 和 `dispatch_agent_stream()`
  - **预计：** 1 天
  - **状态：** 待开始

- [ ] Task 1.2：实现 delegation JWT builder
  - **文件：** `apps/platform-api/app/auth/jwt.py`
  - **函数：** `build_delegation_jwt()`
  - **改动：** 从 platform user 构建 delegation JWT claims
  - **预计：** 0.5 天
  - **状态：** 待开始

- [ ] Task 1.3：实现 webhook 回调接收
  - **文件：** `apps/platform-api/app/api/v1/runtime/webhooks.py`
  - **改动：** 接收 graphharbor 回调，更新 run 状态
  - **预计：** 0.5 天
  - **状态：** 待开始

### Phase 2: 路由层集成（2 个任务）

- [ ] Task 2.1：更新 agent run 路由
  - **文件：** `apps/platform-api/app/api/v1/agents.py`
  - **改动：** `/agents/{agent_id}/runs` 改为调用 `dispatch_agent_run()`
  - **预计：** 1 天
  - **状态：** 待开始

- [ ] Task 2.2：删除分散的直接调用
  - **文件：** `apps/platform-api/app/services/agent_service.py` 等
  - **改动：** 全局搜索 `get_client().runs.create()`，替换为 `dispatch_agent_run()`
  - **预计：** 1 天
  - **状态：** 待开始

### Phase 3: 配置和文档（1 个任务）

- [ ] Task 3.1：配置环境变量和文档
  - **文件：** `apps/platform-api/.env.example`, `apps/platform-api/README.md`
  - **改动：** 新增 `GRAPHHARBOR_API_URL`, `COMPLETION_WEBHOOK_URL` 说明
  - **预计：** 0.5 天
  - **状态：** 待开始

## 验证要求与记录

### 验证要求

- [ ] 单元测试：`tests/runtime/test_dispatch.py` 验证 `dispatch_agent_run()` 构建正确的 SDK 调用
- [ ] 单元测试：`tests/auth/test_jwt.py` 验证 delegation JWT 包含正确的 claims
- [ ] 集成测试：platform-web → platform-api → graphharbor 完整链路，创建 run 成功
- [ ] 集成测试：graphharbor 回调 webhook，platform-api 正确更新 run 状态
- [ ] 集成测试：验证 Protocol v2 stream mode 生效（前端能看到 tools/lifecycle 事件）
- [ ] 回归测试：现有 showcase-demo、deep-agent-demo 等业务场景通过 dispatch 层仍正常工作

### 验证记录

#### 2026-09-09 验证
- 待执行

## 状态

规划中
