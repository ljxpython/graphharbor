# 变更日志 (CHANGELOG)

本文档记录 GraphHarbor（双包 `graphharbor` CLI 与 `graphharbor-runtime`）的关键版本演进与发布历史。

所有包版本严格遵循**双包锁步发版（Lockstep Release）**原则。细分发版说明见 [releases/](releases/) 目录。

## [Unreleased]

- **Bug 修复**：修复 `input.respond` 恢复 Run 时恶意篡改前序 Run `stream_resumable=False` 导致 SSE 重放丢失 `values` 事件的问题。
- **Bug 修复**：修复 `FencedPostgresSaver._writer` 在无 `checkpoint_writer` 上下文时将当前运行中的 Run 误判为外部并发冲突的问题。
- **规范治理**：忽略中文标点歧义告警（RUF001/002/003），格式化所有未对齐代码。

---

## 🚀 活跃与近期发布

### [0.13.0.post39] - 2026-10-05
- **资源修复**：运行结束后回收本地流缓冲，Redis 回放缓存自动过期；长历史按页回放，降低流连接的内存占用。

### [0.13.0.post38] - 2026-10-04
- **Worker 停机**：接入 LangGraph `RunControl` 图级 drain，安全检查点后重排同一 Run 并空输入续跑。

### [0.13.0.post37] - 2026-09-28
- **重大特性**：支持子智能体工具调用历史持久化与定向 `checkpoint_ns` 路由。
- **兼容性放通**：对齐 LangGraph 官方 SDK 的 `POST /state/checkpoint` 端点，彻底解决嵌套子智能体运行轨迹丢失问题。

### [0.13.0.post33] - 2026-09-26
- **架构治理**：运行时与业务边界全面解耦，清理非通用字段。
- **持久化升级**：优化 Postgres checkpoint 状态流转与 SSE 心跳保活机制。

---

## 📦 0.13.0 系列历史发布说明汇总

| 版本 | 说明文档 | 核心变更亮点 |
|---|---|---|
| `0.13.0.post32` | [release-notes-0.13.0.post32.md](releases/0.13.0/release-notes-0.13.0.post32.md) | SSE 事件保活与连接容错优化 |
| `0.13.0.post31` | [release-notes-0.13.0.post31.md](releases/0.13.0/release-notes-0.13.0.post31.md) | PostgreSQL lease 租约异常超时与 reaper 自愈 |
| `0.13.0.post30` | [release-notes-0.13.0.post30.md](releases/0.13.0/release-notes-0.13.0.post30.md) | Redis 队列 worker 并发与事件刷新对齐 |
| `0.13.0.post29` | [release-notes-0.13.0.post29.md](releases/0.13.0/release-notes-0.13.0.post29.md) | v3 event stream 候选特性合入与稳定性修复 |
| `0.13.0.post28` | [release-notes-0.13.0.post28.md](releases/0.13.0/release-notes-0.13.0.post28.md) | Checkpoint 事务变更安全性防护 |
| `0.13.0.post27` | [release-notes-0.13.0.post27.md](releases/0.13.0/release-notes-0.13.0.post27.md) | Agent Server 契约验证套件对齐 |
| `0.13.0.post21` | [release-notes-0.13.0.post21.md](releases/0.13.0/release-notes-0.13.0.post21.md) | 基础协议兼容性补齐与性能优化 |
| `0.13.0.post1~14` | [releases/0.13.0/](releases/0.13.0/) | 早期生产化切片与基础能力逐步迁移记录 |
