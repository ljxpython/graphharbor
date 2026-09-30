# 技术方案：子智能体命名空间状态与历史读取

## 1. 现状事实与问题定位

### 1.1 业务痛点
在多智能体（Multi-agent）派发场景下，主智能体通过调用 `task` 工具动态拉起子图（如 `research`、`general-purpose`）执行子任务。
- **实时执行阶段：** SSE 事件流带有 `namespace: ["tools:<call_id>"]`，前端可以实时渲染子智能体的各种工具调用（如 `ls`、`read_file`、`grep` 等）。
- **历史恢复阶段：** 页面刷新或重新加载历史时，客户端调用 `GET /threads/{id}/state` 或 `POST /threads/{id}/history`。GraphHarbor 仅返回根命名空间（`checkpoint_ns = ""`），主图上仅留下一条 `ToolMessage (name="task")` 聚合文本。所有子智能体调用过的工具、命令、文件路径及报错完全丢失。

### 1.2 底层存储事实（已核实）
PostgreSQL 存储层（`checkpoints`、`checkpoint_blobs` 表）已经具备完整的复合主键/索引 `(thread_id, checkpoint_ns)`，并且子图在执行过程中产生的每个 checkpoint 及其消息 blobs 均已 100% 完整持久化，没有任何数据丢失。

### 1.3 GraphHarbor 现有代码漏洞定位
在 `libs/langhost/src/langhost/core_api.py` 中：
1. `_checkpoint_config(thread_id, checkpoint_id)` 写死了 `configurable = {"thread_id": str(thread_id)}`，没有任何接收与传递 `checkpoint_ns` 的能力。
2. `threads_state` 仅从路径或 body 中提取了 `checkpoint_id`，直接忽略了请求中提供的 `checkpoint.checkpoint_ns`。
3. `threads_history` 仅调用 `_checkpoint_config(thread_id)`，完全未从请求 payload 的 `checkpoint` 对象中读取 `checkpoint_ns`。
4. 这导致客户端即使按照官方 LangGraph SDK 规范发起请求，参数在 GraphHarbor 状态层也被截断丢弃，恒定降级为查询根图 `checkpoint_ns=""`。

---

## 2. 方案对比与仲裁

### 2.1 方案 1：`expand_subagents=true` 自动聚合（驳回）

**使用方提议：** 在 `GET /threads/{id}/state` 增加 `expand_subagents=true`，并在 `ThreadState` 中添加 `subagents` 字典。

**驳回理由：**
1. **违反官方契约原则：** 破坏了 LangGraph OpenAPI 与 SDK 的标准 `ThreadState` 响应模型。下游官方 SDK 及强类型客户端遇到非预期字段会产生兼容问题。
2. **违背通用 Agent Server 边界：** 方案 1 强假设子图都是 `LIKE 'tools:%'` 且带 `trigger_call_id`。GraphHarbor 作为通用 Agent Server，不得侵入特定业务命名模式与上层工具封装约定。
3. **性能风险与内存放大：** 子智能体工具调用密集时可能包含成百上千条消息与工具结果。一次性将整个 Session 内的所有子图全量反序列化并塞入单个 JSON，容易导致接口高延迟、高并发内存溢出。
4. **缺失历史溯源能力：** 方案 1 无法支持 `threads_history`，客户端无法对子智能体内部步骤进行步进时光倒流。

### 2.2 方案 2：对齐官方 SDK 标准命名空间查询（采纳并增强）

**设计思路：** 遵循 LangGraph 官方规范，全面支持在 State 与 History 接口中指定 `checkpoint_ns`。

#### 核心技术难点与避坑方案：Pregel 动态 Subgraph 陷阱
LangGraph 内部的 `CompiledStateGraph.aget_state` 和 `aget_state_history` 会检查 `checkpoint_ns`：
- 若 `checkpoint_ns` 非空，主图会遍历其静态编译拓扑（`aget_subgraphs`）。
- **然而：** Tool-delegated subagent 是在工具函数内部动态构建并调起的，主图的静态拓扑中并不包含该子图。
- 直接对主图调用 `graph.aget_state(config)` 会触发：`ValueError: Subgraph <recast_ns> not found`，导致 GraphHarbor 抛出 `503 checkpoint read failed`。

**解决策略（智能分流）：**
- 当客户端请求 `checkpoint_ns` 为空（根图）时：若图在 `graph_registry` 中，继续走 `graph.aget_state`，获得最新的 delta channel 投射与 tasks。
- 当客户端请求明确指定了非空 `checkpoint_ns` 时：绕过静态图拓扑查找，直接调用底层 checkpointer（`checkpointer.aget_tuple(config)` 或 `checkpointer.alist(config)`）。底层 PostgreSQL checkpointer 原生支持按 `(thread_id, checkpoint_ns)` 精确查询并反序列化，安全稳定且性能极高。

---

## 3. 接口契约规范

### 3.1 查询指定命名空间的最新状态 (State)

#### 方式 A：标准 SDK POST 端点（推荐）
```http
POST /threads/{thread_id}/state/checkpoint
Content-Type: application/json

{
  "checkpoint": {
    "checkpoint_ns": "tools:89b00bd9-03c0-6b3a-4dc0-566fe060a451",
    "checkpoint_id": "1f1bad43-84ff-6078-8020-ad4e4fa8610c"  // 可选，不传查最新
  }
}
```

#### 方式 B：GET Query 参数简写
```http
GET /threads/{thread_id}/state?checkpoint_ns=tools:89b00bd9-03c0-6b3a-4dc0-566fe060a451
```

**响应：** 标准 `ThreadState` 结构体，`values` 中包含该子智能体完整上下文消息（包括 `AIMessage.tool_calls` 与全部 `ToolMessage`）。

---

### 3.2 查询指定命名空间的历史轨迹 (History)

```http
POST /threads/{thread_id}/history
Content-Type: application/json

{
  "checkpoint": {
    "checkpoint_ns": "tools:89b00bd9-03c0-6b3a-4dc0-566fe060a451"
  },
  "limit": 20
}
```

**响应：** 标准 `list[ThreadState]`，按时间倒序排列的子智能体历史快照序列。

---

## 4. 客户端交互推荐模式（懒加载）

为避免一次性拉取全部子智能体数据造成页面卡顿，建议前端采取如下交互逻辑：
1. 页面初次加载调用 `GET /threads/{id}/state`，获取根图消息。
2. 渲染消息流时，识别出 `tool_calls` 派生的子智能体（如 `name == "task"` 的 `ToolMessage`，其 `tool_call_id` 为 `call_xxx`）。
3. 前端界面展示“子智能体执行轨迹（展开查看）”折叠卡片。
4. 用户点击展开时，前端异步请求：
   `POST /threads/{id}/state/checkpoint`，参数 `{"checkpoint": {"checkpoint_ns": "tools:call_xxx"}}`。
5. 收到子图消息后平铺展示子智能体的每一次真实工具调用（如 `read_file`、`grep` 等参数与返回值）。

---

## 5. 风险与回滚方案

- **风险评估：**
  - 改动仅在已有状态读取接口上补充参数解析分支，对现有未传 `checkpoint_ns` 的请求行为完全不改变，风险极低。
  - 数据安全：状态接口受现有的 `_get_thread(request)` 和租户权限（`in_principal_scope`）管控，子图 Checkpoint 归属同一 `thread_id`，天然受到相同权限隔离保护。
- **回滚方式：**
  - 属于无状态接口代码修复，无需数据库回滚，若有异常直接回滚对应代码改动即可。
