# 变更日志 (CHANGELOG)

本文档记录 GraphHarbor（双包 `graphharbor` CLI 与 `graphharbor-runtime`）的关键版本演进与发布历史。

所有包版本严格遵循**双包锁步发版（Lockstep Release）**原则。细分发版说明见 [releases/](releases/) 目录。

## [Unreleased]

- 新Run不再复制整条会话的checkpoint/writes和完整结果投影；显式裁剪回收无引用blob并保留Delta恢复祖先。单格式migration015提供一次性转换与备份恢复路径，长历史维护成本及数据恢复已有隔离PG证据。
- 修复Worker快速领取Run时SSE首帧将首次执行错误显示为第2次的问题。

- 会话支持固定/闲置到期、显式活动续期，以及手动和自动共用的可靠删除；清理交接失败可重试、重启后继续，完成后可精简墓碑 metadata。正式双包 `0.13.0.post48` 已直接发布并完成 PyPI 冷安装及使用方隔离栈验证，见[发布说明](releases/0.13.0/release-notes-0.13.0.post48.md)。

- 新增通用 Run 接受回执：严格绑定原始请求、原子保存接受/拒绝事实、固定 key 只读回查和永久防重墓碑；双包 `0.13.0.post45` 已直接发布 PyPI，四产物 SHA256、正式 wheel/sdist 独立安装与使用方 notify/Stop/撤权联合验收通过。

- **Run completion webhook**：新增通用 Run/Cron 原生 webhook 配置、原子 terminal snapshot/outbox、HMAC 受管 delivery、lease/retry/dead-letter/replay、网络目标安全与运维 CLI；不包含任何平台业务通知或模型供应商逻辑。`0.13.0.post44` 已发布 PyPI，使用方前端与正式三服务整链路另行验收。

- 会话元数据支持数据库原子合并与独立条件更新，避免并发修改互相覆盖；`0.13.0.post44` 正式双包已包含该通用能力，独立使用方验收仍由 [CAS 专项](projects/20261009-thread-metadata-cas/README.md) 维护。

- 会话可原子取消当时全部已接受的活动/排队Run，并以固定持久回执查询实际退出；同动作重试不取消后来的新Run。`0.13.0.post44` 正式双包已包含 migration011，独立使用方验收仍由[取消专项](projects/20261007-thread-cancellation/README.md)维护。
- **Bug 修复**：修复 `input.respond` 恢复 Run 时恶意篡改前序 Run `stream_resumable=False` 导致 SSE 重放丢失 `values` 事件的问题。
- **Bug 修复**：修复 `FencedPostgresSaver._writer` 在无 `checkpoint_writer` 上下文时将当前运行中的 Run 误判为外部并发冲突的问题。
- **规范治理**：忽略中文标点歧义告警（RUF001/002/003），格式化所有未对齐代码。

---

## 🚀 活跃与近期发布

### [0.13.0.post42] - 2026-10-07

- 取消运行即时通知 Worker；停止等待、SSE 和 join 在执行资源清理与持久停止提交后确认，清理期间继续保护会话，崩溃恢复不会误报已停止。
- 取消受理返回202，停止确认成功返回200，无法确认返回503；支持interrupt/rollback和Threadless，API/Worker须锁步升级。
- Worker按每attempt时限运行，模型/provider超时为error、硬限为timeout；DB瞬时故障从checkpoint有界恢复，正常交接不消耗故障attempt。使用方已完成正式包超时/恢复及匹配版本回退验收，前端与联合Final另验。
- 双包wheel/sdist已直接发布PyPI；详见[发布说明](releases/0.13.0/release-notes-0.13.0.post42.md)。

### [0.13.0.post41] - 2026-10-05

- **Cron 修复**：有限计划保留最后一次运行；编辑执行参数使用当前身份，暂停保留原执行身份。
- **运行恢复**：并发创建同一会话保持唯一；会话删除后已排队运行明确失败并保留记录。
- **运行查询**：内部仓储支持按元数据过滤后分页，供应用复用。

### [0.13.0.post40] - 2026-10-05

- **Cron 调度**：官方 SDK 周期任务自动执行、运行关联、暂停/删除；支持时区、截止时间、多实例领取和服务重启恢复。

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
