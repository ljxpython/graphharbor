# 项目当前状态 - AI 上下文快照

> **AI 读取规则：** 每次新会话开始前主动读取此文件；任务完成后按需更新对应状态行。
> **维护规则：** 只保留“当前有效”事实，过期内容一律删除或归入 `docs/archive/`。

---

## 📌 当前版本与里程碑

- **当前双包锁步版本**：`0.13.0.post37` (`libs/langhost` 与 `libs/langgraph-runtime-pg`)
- **最后更新时间**：2026-09-30
- **最新里程碑**：
  - **2026-09-30** | 全链路 CI 硬伤彻底根治（Run #36666991389 全绿）：彻底攻克生产契约重播 values 丢失（移除 input.respond 暴力重置 stream_resumable）、E2E 节点保存误杀 CheckpointConflict（精确放行活跃 Run 自身 Checkpoint 写入）以及全仓 Lint/mypy 规范，7 项矩阵任务 100% 绿灯通过。
  - **2026-09-30** | AI Harness 六层工业级体系全面落成：对标全网最高标准，完成约束层（AGENTS.md 双包路由与反模式禁令）、流程层（.codex/skills/ 实装 Task Completion Card、两阶段验证与四态判定）、记忆层（docs/CONTEXT.md 会话快照、docs/FEATURES.md 语义记忆总览、docs/standards/ 契约健康表与置信度元数据）、可观测层（四处状态一致性核对）以及反馈层（docs/lessons/ 蒸馏经验库）。打通 .agents/skills 软链接双通道统一寻路。
  - **2026-09-30** | 文档体系全面重构：对标企业级 AI Agent 标准规范，完成人机分流导航设计。建立 `docs/README.md`、`docs/CONTEXT.md`、`docs/CHANGELOG.md`；收拢 20+ 碎片发布日志至 `releases/`；归整 `compatibility/`、`runbooks/` 与 `standards/`；预留 `architecture/` 系统架构专区。
  - **2026-09-28** | 子智能体工具历史持久化（`0.13.0.post37`）：支持定向 `checkpoint_ns` 路由与 `POST /state/checkpoint` 端点，彻底解决嵌套子智能体运行轨迹丢失问题。
  - **2026-09-27** | SSE 事件流保活心跳与连接容错优化（`0.13.0.post32`）：优化长连接断流重连与审批状态机自愈。

---

## 📦 双包组件当前状态

| 组件/包 | 当前版本 | 源码位置 | 关键约束与职责 |
|---|---|---|---|
| **`graphharbor`** (CLI) | `0.13.0.post37` | `libs/langhost/` | 承载 CLI (`graphharbor serve`) 与 ASGI HTTP/SSE 网关边界，负责 Core Protocol 兼容与路由调度 |
| **`graphharbor-runtime`** | `0.13.0.post37` | `libs/langgraph-runtime-pg/` | PostgreSQL 状态机（Checkpoints/Lease/Reaper）与 Redis 分布式 Worker 核心引擎 |

---

## 🚀 活跃与近期核心专项

- [子智能体 Checkpoint Namespace 历史持久化](projects/20260928-subagent-checkpoint-namespace-history/README.md)：`done`；双包 post37 升级并放通 `checkpoint_ns`，子智能体工具调用历史 100% 可查。
- [SSE 事件流保活心跳与连接容错](projects/20260927-sse-stream-heartbeat-and-resilience/README.md)：`done`；网关保活心跳注入与前端容错对齐。
- [Runtime 业务边界解耦与脱敏](projects/20260925-runtime-business-boundary-decoupling/README.md)：`partial`；通用 Agent Server 边界治理，消除存量业务字段。
- [Runtime 事件保留与清理](projects/20260925-runtime-event-retention/README.md)：`done`；优化事件存储周期与重放性能。
- [Checkpoint 事务变更安全性](projects/20260924-checkpoint-mutation-safety/README.md)：`done`；杜绝非幂等写入与状态污染。
- [Worker 并发与事件刷新](projects/20260923-worker-concurrency-event-flush/README.md)：`done`；Redis 队列并发模型与缓冲刷新。
- [通用业务边界隔离](projects/20260909-graphharbor-business-boundary-separation/README.md)：`partial`；存量追踪字段与专有逻辑隔离中。

---

## ⛔ 核心设计原则与架构红线

1. **通用 Agent Server 定位（业务边界红线）**：
   - 核心包、CLI、API、事件协议和数据库模型**只能承载通用运行时概念**。
   - 严禁加入特定业务的模型供应商、模型名、特定工具名、业务策略或 trace 字段。
2. **双包锁步发版（Lockstep Packaging）**：
   - `graphharbor` 与 `graphharbor-runtime` 必须严格保持相同版本，严禁单包私自跳版本。
3. **测试数据安全防护**：
   - `scripts/test.sh` 会执行 drop table 清空数据库，**只能在独立隔离的本地或 CI 测试库中运行**，严禁在包含重要数据的环境执行。
