# 子智能体（Subagent）执行轨迹与 Checkpoint 命名空间历史读取

- **项目标识：** `20260928-subagent-checkpoint-namespace-history`
- **对应提案：** `RFC-20260928-GH-SUBAGENT-HISTORY`
- **当前状态：** 已发布 (Released) - `0.13.0.post37`
- **负责人：** GraphHarbor 核心研发团队

---

## 1. 一句话目标

修复 GraphHarbor 状态与历史接口中被遗漏的 `checkpoint_ns` 命名空间透传能力，绕过 LangGraph 动态子图静态拓扑解析崩溃限制，支持客户端与官方 LangGraph SDK 按命名空间拉取子智能体（Subagent）的完整工具调用历史与执行状态。

---

## 2. 影响范围

- **公开契约面：**
  - `POST /threads/{thread_id}/state/checkpoint`：完整支持 payload 中的 `checkpoint.checkpoint_ns`。
  - `GET /threads/{thread_id}/state`：增加对 query 参数 `checkpoint_ns` 的支持。
  - `POST /threads/{thread_id}/history`：完整支持 payload 中的 `checkpoint.checkpoint_ns`，支持子图时光倒流与历史回溯。
  - 严格保持与官方 `langgraph-sdk` 的 `threads.get_state(checkpoint={"checkpoint_ns": ...})` 和 `threads.get_history(checkpoint={"checkpoint_ns": ...})` 协议 100% 兼容。
- **内部核心代码：**
  - `libs/langhost/src/langhost/core_api.py`：`_checkpoint_config`、`threads_state`、`threads_history`。
- **质量与测试：**
  - `libs/langhost/tests/test_subagent_history.py`（新增针对子命名空间 State 和 History 的完整单元/集成测试）。
- **非影响范围：**
  - 不修改 PostgreSQL 底层表结构（已具备 `(thread_id, checkpoint_ns)` 索引）。
  - 不引入私有自定义响应体（坚决不向 `ThreadState` 注入非官方 `subagents` 字段）。

---

## 3. 文档导航

- [架构方案与技术分析 (plan.md)](plan.md)
- [实施任务拆解 (tasks.md)](tasks.md)
- [验证方案与验收证据 (verification.md)](verification.md)
- [业务方官方答复与接入指引 (integration-guide.md)](integration-guide.md)

---

## 4. 关键决策与结论

1. **驳回方案 1（`expand_subagents=true` 全量聚合）：**
   - 违背 `compatibility-profile.md` 中“官方 SDK/REST 调用不改”的底线，破坏 `ThreadState` Schema 标准。
   - 存在 N+1 大 Blob 反序列化和网络传输打爆风险。
   - 无法覆盖 `history` 历史溯源场景。
2. **采纳并增强方案 2（官方标准 `checkpoint_ns` 支持）：**
   - 对齐官方 SDK 标准：`threads.get_state(..., checkpoint={"checkpoint_ns": ...})`。
   - 对齐官方 API 标准：`POST /threads/{id}/state/checkpoint` 与 `POST /threads/{id}/history`。
3. **解决 Pregel 动态 Subgraph 报错陷阱：**
   - LangGraph 的 `graph.aget_state` 会在拓扑中遍历静态子图，动态 Tool 调起的 Subagent 会导致 `ValueError: Subgraph not found`。
   - 决策：当检测到非空 `checkpoint_ns` 且主图未静态注册该子图时，智能绕过 `graph`，直接回退到底层 PostgreSQL Checkpointer 读取（`checkpointer.aget_tuple` / `checkpointer.alist`）。
4. **前端接入推荐最佳实践：**
   - 前端采用**按需懒加载**模式：检测到主图 `ToolMessage(name="task", tool_call_id=xxx)` 时，通过 `POST /threads/{id}/state/checkpoint` 拉取 `checkpoint_ns: "tools:<tool_call_id>"`，兼顾首屏速度与完整审计溯源。
