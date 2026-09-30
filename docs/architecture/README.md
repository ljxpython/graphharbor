# GraphHarbor 系统架构文档

> 📌 本目录作为系统核心架构教学与原理文档库，当前内容留空，后续由团队自行补充与完善。

## 预留规划章节建议

- **01. 系统全貌与双包分层 (Overview & Packaging)**：
  - `libs/langhost`：GraphHarbor CLI 与 ASGI 协议网关边界。
  - `libs/langgraph-runtime-pg`：PostgreSQL 状态存储与 Redis 分布式调度核心引擎。
- **02. 持久化与租赁机制 (Persistence & Lease Engine)**：
  - PostgreSQL Checkpoint 状态存储与事务原子性。
  - Lease 管理、Heartbeat 与过期任务收割（Reaper）机制。
- **03. Worker 调度与流式事件 (Worker & Streaming Lifecycle)**：
  - Redis 队列消费、Pub/Sub 事件总线。
  - SSE 事件协议、断线续传（Last-Event-ID）与 Run 状态机。
- **04. 多租户与权限隔离边界 (Multi-tenancy & Isolation)**：
  - Generic Agent Server 通用性原则与非业务边界红线。
