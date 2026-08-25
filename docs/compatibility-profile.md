# GraphHarbor Compatibility Profile

## 1. 目的

GraphHarbor 不要求第一天实现官方 Agent Server 的所有边缘能力，但必须对已经声明支持的能力做到完整兼容：

~~~text
用户的 graph 不改
langgraph.json 不改
官方 Python/JavaScript SDK 不改
官方 REST/SSE 调用不改
当前项目 custom routes/auth 不改
~~~

因此采用分层 Compatibility Profile，而不是一次性建立全量官方协议基线。

## 2. 兼容等级

### Level Core

Level Core 是第一个可交付版本的硬性范围。使用这些能力时，用户不需要增加适配层。

### Level Extended

Level Extended 是已知的官方能力，但不是第一阶段发布门禁。进入实现时必须增加独立契约和 E2E 测试。

### Level Unavailable

暂不支持的能力必须：

- 在文档和能力信息中明确标记。
- 返回清晰的 404/501 或官方对应错误。
- 不返回伪造的成功结果。
- 不改变 Core 能力的行为。
- 不对外宣称完整 Agent Server 兼容。

## 3. Level Core 范围

### 3.1 配置和发现

必须支持：

- 标准 langgraph.json。
- dependencies、graphs、env。
- auth、http.app。
- graph discovery 和 assistant/graph lookup。
- /ok、/info、/openapi.json、/docs（Scalar API Reference）。

验证：

1. 使用当前 runtime_service/langgraph.json 原文件启动。
2. 不修改 graph 导出路径。
3. 官方 SDK 能发现 assistant。
4. Studio smoke 能读取 graph 和服务信息。

### 3.2 Threads、assistants、runs

Core 覆盖当前客户端和当前项目真实链路需要的资源族：

~~~text
assistants：发现、读取、创建/更新（目标 SDK 需要的范围）
threads：创建、读取、搜索、更新、删除
thread state：读取、更新、checkpoint 读取
thread history：读取
runs：创建、读取、等待、流式运行、取消
thread runs：创建、读取、等待、流式运行、取消
~~~

具体路径、请求模型、响应模型和错误码以目标 LangGraph SDK/OpenAPI 版本为准，不凭旧版本文档猜测。

每个资源族验证：

- 正常请求。
- 参数校验失败。
- 未认证、无权限、跨租户资源。
- 资源不存在。
- 重复请求。
- 服务重启后的读取。

### 3.3 v2 普通远程流

第一阶段保留官方调用：

~~~python
client.runs.stream(
    thread_id,
    assistant_id,
    input=inputs,
    version="v2",
)
~~~

Core 覆盖：

- messages、updates、values、custom、debug。
- 多模式组合。
- token/content-block。
- final state、error、terminal event。
- SSE heartbeat。
- run 断线后的 replay/cursor。

要求：

- 官方 SDK 解析成功。
- 事件顺序稳定。
- run_id、thread_id、namespace 和 metadata 不丢失。
- 多 worker、多 API 实例行为一致。

### 3.4 v3 事件流、子图和 HITL

Core 包含未来前端需要的事件能力：

~~~python
graph.stream_events(..., version="v3")
~~~

远程 server 必须提供官方客户端可消费的等价能力，不能要求前端使用 GraphHarbor 私有 parser。

必须支持：

- 主图和子图 lifecycle。
- 子图 path/namespace。
- messages、values、updates、custom、debug/events projection。
- interrupt requested、resume accepted。
- completed、failed、interrupted。
- sequence/cursor、reconnect/replay。

### 3.5 HITL

官方语义：

~~~text
interrupt(payload)
→ 持久化 thread/checkpoint
→ 返回 interrupt
→ 人工决定
→ Command(resume=...)
→ 同一 thread 继续
~~~

Core 必须验证：

- approve、reject、edit、respond。
- 多次顺序 interrupt。
- 多个 pending interrupt 的标识。
- 重复 resume 幂等。
- cancel 与 interrupted 区分。
- 服务重启、worker kill、客户端断线后继续。

### 3.6 当前应用边界

必须保留：

- runtime_service/langgraph.json。
- 所有当前 graph。
- 所有 custom routes。
- runtime_service/auth/platform.py 和 auth/provider.py。
- RuntimeContext 和 RuntimeRequestMiddleware 契约。
- 当前前端依赖的请求和响应语义。

### 3.7 Runtime 和运维

Core 覆盖：

- PostgreSQL threads/runs/checkpoint。
- Redis job queue、Pub/Sub、replay。
- worker lease、heartbeat、reaper。
- cancel、retry、graceful shutdown/requeue。
- 单实例和多实例。

## 4. Level Extended 范围

以下能力不阻塞 Core 第一版，但必须保留扩展位：

~~~text
store / semantic store
cron
webhook
MCP
A2A
Generative UI
高级管理 API
多数据库/多区域 HA
~~~

进入 Extended 时必须补齐：

1. 目标版本 API/SDK 语义。
2. 认证和租户规则。
3. PostgreSQL/Redis 数据语义。
4. 单元、HTTP/SDK、multi-worker 和故障测试。
5. 文档和迁移说明。

## 5. 兼容声明

未完成 Core 前，只能声明：

~~~text
GraphHarbor Compatibility Profile: experimental
~~~

Core 完成后可以声明：

~~~text
GraphHarbor Compatibility Profile:
Core Runs + Streaming v2 + Events v3 + HITL + Subgraphs
~~~

同时必须声明 GraphHarbor、LangGraph SDK、Python、PostgreSQL、Redis 版本，以及 Extended/Unavailable 清单。

未来官方新增能力不能自动算作已支持。

## 6. 垂直切片实施

每个切片都沿完整链路实现：

~~~text
官方客户端
→ HTTP/SSE
→ Agent Protocol server
→ auth/tenant
→ queue/worker
→ graph execution
→ PostgreSQL/Redis
→ event/replay
~~~

### Slice A：基础资源

实现：config/discovery、assistants、threads、state/history、runs。

验证：Python SDK、JavaScript SDK、REST、服务重启、PG 恢复、auth/tenant。

### Slice B：v2 远程流

实现：run stream、stream modes、Redis Pub/Sub、SSE heartbeat、replay/cursor、terminal/error event。

验证：官方 Python/JavaScript SDK不改代码；两个 API 实例；两个 worker；客户端断线重连。

### Slice C：v3 事件投影

实现：typed projections、lifecycle、subgraphs、namespace/path、custom/debug/events。

验证：主图、Deep Agent 子图、supervisor 子图、并发事件、错误事件、事件顺序。

### Slice D：HITL

实现：interrupt、checkpoint、commands/resume、approve/reject/edit/respond、resume 幂等、cancel/interrupted 状态。

验证：断线、server 重启、worker kill、多次 interrupt、重复 resume。

### Slice E：custom routes/auth/lifespan

实现：core/runtime/custom lifespan 合并、Principal、Auth、custom route auth、CORS、readiness/shutdown。

验证：所有 custom routes、401/403/404、跨租户隔离、启动顺序、关闭顺序、资源清理。

### Slice F：生产可靠性

实现：lease/reaper、retry、cancel、graceful drain/requeue、PG/Redis failure handling、multi-instance routing。

验证：worker/API kill、Redis/PG restart、rolling deployment、queue backlog。

## 7. 切片完成定义

一个切片只有同时满足以下条件才算完成：

1. server 端能力已实现。
2. runtime 数据语义已固定。
3. auth/tenant 边界已固定。
4. 官方客户端不需要修改。
5. 单元测试通过。
6. HTTP/SDK E2E 通过。
7. 故障场景通过。
8. OpenAPI/文档已更新。
9. capability/profile 已更新。
10. 未支持能力没有被误报为成功。

## 8. 已冻结的局部契约

六项 owner 决策已经完成，切片实施使用以下固定约束：

1. 目标版本主线为 Python 3.13、CI 兼容 3.11/3.12；`langgraph 1.2.11`、`langgraph-sdk 0.4.3`，`langgraph-api 0.13.0` 仅用于内部 spike。
2. Core 资源族包含当前 adapter/route/前端调用并集；`store` 保持 Extended。
3. Principal 由 platform-api delegation JWT 产生，custom routes 共用同一 Principal。
4. 对外没有独立 `cancelled` run status；cancel/HITL 均映射为官方 `interrupted`。
5. 默认 `multitask_strategy="enqueue"`，基础设施错误最多自动重试 3 次。
6. 首阶段使用本地 API、worker 与独立 PostgreSQL/Redis 部署；不提供容器编排资源。
7. P0 graph、目标并发、SSE 连接数、P95 延迟、数据保留期和旧数据迁移属于实施/运行参数，按阶段门禁补齐。

当前项目已经通过 `platform-api` adapter 使用 `/runs/batch`、`/runs/cancel`、`/threads/count`、`/threads/{thread_id}/copy`、`threads/prune` 和 cron 操作，因此这些能力属于 Core，不能再按“当前前端暂时没有点击”放入 Extended。

这些是切片级契约，不是重新讨论整个架构。

## 9. 当前可以开始什么

可以开始：

- 生成 Core Compatibility Profile。
- 查询最新 LangGraph 依赖。
- 建立官方 SDK smoke。
- 建立当前 server/runtime baseline。
- 做内部 compatibility spike。
- 验证 PostgreSQL、Redis 和 runtime。

不能开始：

- 宣称完整 drop-in。
- 对外发布生产版本。
- 只实现当前项目调用过的接口就宣布兼容。
- 根据旧版 langgraph-api 猜新版本协议。

## 10. 正式实施门禁

正式进入代码实施前已冻结：

~~~text
1. Core Compatibility Profile
2. 目标 LangGraph/SDK 版本
3. 当前项目真实调用清单
4. Principal/auth contract
5. run/HITL/cancel 状态机
6. 首阶段部署和验收环境
~~~

不需要提前实现官方所有协议，但这六项必须明确。
