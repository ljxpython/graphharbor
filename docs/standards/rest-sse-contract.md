---
status: active
last_verified: 2026-09-30
confidence: high
source_project: docs/projects/20260927-sse-stream-heartbeat-and-resilience/
---

# Core REST 与 SSE 事件流契约标准 (rest-sse-contract.md)

本文档定义 GraphHarbor 对外暴露的 Core REST API 端点语义、SSE 流式事件协议、保活心跳机制与断线续传契约。

---

## 1. 协议核心原则

1. **零代码侵入**：与 LangGraph 官方 Core Agent Server 规范保持 100% 协议兼容，官方 Python/JS SDK 无需任何修改即可直连。
2. **长连接保活**：网关层必须定时注入保活心跳帧，杜绝生产反向代理因长时空闲切断连接。
3. **断线精准续传**：基于事件单调递增 ID，客户端断开后通过 `Last-Event-ID` 请求头可精准补全丢失事件，避免状态重复消费。

---

## 2. SSE 事件帧结构与命名规范

所有流式响应遵循标准 `text/event-stream` 格式：

```text
id: {event_id}
event: {event_type}
data: {json_payload}

```

### 事件类型 (Event Types)
- **`metadata`**：Run 启动时的元数据描述帧（包含 run_id, assistant_id 等）。
- **`values`**：图节点产生完整状态更新时的状态帧。
- **`updates`**：图节点产生的增量状态更新帧。
- **`messages`**：流式产出的大模型 Message/Token 块。
- **`custom`**：用户图内部通过 `get_sync_writer()` 发送的自定义事件。
- **`ping` (保活心跳)**：网关内部心跳帧（见下文）。
- **`end`**：流正常结束终态标记帧。

---

## 3. 网关保活心跳机制 (Keep-Alive Heartbeat)

- **心跳间隔**：网关在流式传输期间，若连续 **15 秒**未向客户端输出有效业务事件，必须强制注入一条保活心跳帧：
  ```text
  event: ping
  data: {"timestamp": 1727670000.0}
  ```
- **客户端行为**：官方 SDK 及浏览器 EventSource 收到 `ping` 事件自动维持底层 TCP 活跃，静默丢弃或忽略该数据，不触发应用层业务报错。
- **设计防护**：彻底根治 Nginx / Ingress / ALB 默认 45~60 秒的空闲连接断开问题（消除 HTTP 504 / Connection reset by peer）。

---

## 4. 断线无感续传 (Resumption via Last-Event-ID)

- **请求头**：客户端重连时通过 HTTP Header `Last-Event-ID: {event_id}` 发起请求。
- **服务端处理**：
  1. 网关优先在 Redis 缓冲队列中检索该 ID 之后的增量事件流；
  2. 若已溢出缓存，从 PostgreSQL 事件归档表中按顺序回放；
  3. 回放完成后，无缝接入当前实时事件广播流。
- **已决审批中断处理**：对于历史已被处理并恢复的 HITL `interrupt` 事件，重播时标记 `resolved: true`，防止前端界面二次弹出死锁。

---

## 5. 错误响应标准 Envelope

当请求发生异常或未授权时，网关返回符合 Core 协议的标准 JSON 响应：

```json
{
  "message": "资源不存在或已被清理",
  "detail": {
    "error_code": "RESOURCE_NOT_FOUND",
    "thread_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }
}
```
- 严格遵循 HTTP 语义状态码：400 参数错误、401 未认证、403 无权限、404 未找到、409 状态并发冲突、500 服务端内部错误。
