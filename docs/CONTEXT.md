# 项目当前状态 - AI 上下文快照

> **AI 读取规则：** 每次新会话开始前主动读取此文件；任务完成后按需更新对应状态行。
> **维护规则：** 只保留“当前有效”事实，过期内容一律删除或归入 `docs/archive/`。

---

## 📌 当前版本与里程碑

- **当前正式双包版本**：2026-11-21直接发布PyPI `0.13.0.post49`，最小Run边界、首次before-image、JSON Patch反向投影、安全prune/blob GC与migration015；已有TTL/接受回执/webhook/CAS/取消原语保持。四产物SHA256与正式源相同。
- **当前源码版本**：双包锁步post49；最终wheel源码逐字节匹配当前源码，未混入平台或私有文件。源码扩大399passed/22skipped、独立Cron3passed，最终候选关键37项、sdist26项及stream11项通过；正式源冷安装42passed/1deselected，另有HTTP/Python/JS取消24场景。套件重叠不加总，内部上游ops需可选langgraph-api不在独立安装范围，严格官方对照仍有既有差异。
- **最后更新时间**：2026-11-21（存储治理S01–S07按批准范围done；正式post49/head015已交接。未执行本仓Git或现役部署；平台Worktree接入及业务验收由关联项目负责。）
- **最新里程碑**：
  - **2026-09-30** | 工业级架构深潜文档体系全量落成（`docs/architecture/`）：以老王暴躁技术流方法论为主导，圆满交付全局架构深潜中心与概念总字典（`docs/architecture/README.md`），覆盖 6 大核心主干模块（01-06）与 11 篇深度概念专篇，系统性攻克 Monorepo 双包锁步、官方协议黑盒适配、PostgreSQL 代际防裂脑、Redis 分布式租约收割、Graph 确定性状态机与离线 Mock 测试隔离体系。
  - **2026-09-30** | 全链路 CI 硬伤彻底根治（Run #36666991389 全绿）：彻底攻克生产契约重播 values 丢失（移除 input.respond 暴力重置 stream_resumable）、E2E 节点保存误杀 CheckpointConflict（精确放行活跃 Run 自身 Checkpoint 写入）以及全仓 Lint/mypy 规范，7 项矩阵任务 100% 绿灯通过。
  - **2026-09-30** | AI Harness 六层工业级体系全面落成：对标全网最高标准，完成约束层（AGENTS.md 双包路由与反模式禁令）、流程层（.codex/skills/ 实装 Task Completion Card、两阶段验证与四态判定）、记忆层（docs/CONTEXT.md 会话快照、docs/FEATURES.md 语义记忆总览、docs/standards/ 契约健康表与置信度元数据）、可观测层（四处状态一致性核对）以及反馈层（docs/lessons/ 蒸馏经验库）。打通 .agents/skills 软链接双通道统一寻路。
  - **2026-09-30** | 文档体系全面重构：对标企业级 AI Agent 标准规范，完成人机分流导航设计。建立 `docs/README.md`、`docs/CONTEXT.md`、`docs/CHANGELOG.md`；收拢 20+ 碎片发布日志至 `releases/`；归整 `compatibility/`、`runbooks/` 与 `standards/`；预留 `architecture/` 系统架构专区。
  - **2026-09-28** | 子智能体工具历史持久化（`0.13.0.post37`）：支持定向 `checkpoint_ns` 路由与 `POST /state/checkpoint` 端点，彻底解决嵌套子智能体运行轨迹丢失问题。
  - **2026-09-27** | SSE 事件流保活心跳与连接容错优化（`0.13.0.post32`）：优化长连接断流重连与审批状态机自愈。

---

## 📦 双包组件当前状态

| 组件/包 | 当前版本 | 源码位置 | 关键约束与职责 |
|---|---|---|---|
| **`graphharbor`** (CLI) | 0.13.0.post49 | `libs/langhost/` | 正式PyPI已发布/冷安装；逆序回滚使用sequence，SSE首帧attempt竞态已修；现役未升级 |
| **`graphharbor-runtime`** | 0.13.0.post49 | `libs/langgraph-runtime-pg/` | journal-v1、反向投影、引用安全prune/blob GC与migration015；六组预算及正式PG验证通过；无业务政策 |

历史post42取消与attempt门禁、12组HTTP、新PID接管及匹配post41回退证据仍见各专项；不据此宣称本轮完成其他专项回归。post44 产物核验确认 CAS 独立 route/OpenAPI 与 JSONB 共享 helper 已发布，不新增 Schema/平台业务字段；CAS 使用方正式接入是否完成仍由该专项验收。

---

## 🚀 活跃与近期核心专项

- [Checkpoint 存储增长治理](projects/20261120-checkpoint-storage-governance/README.md)：已完成（done，S01–S07）。正式post49、head015、独立冷安装42项和六组容量通过；旧10,000×16KiB边界触发JSONB超限，新边界最大330B。严格官方逐字段差异保留，不承诺永久状态有界。核心纯通用，Git和现役部署未执行，平台关联项目独立验收。

- [Checkpoint 事务变更安全性](projects/20260924-checkpoint-mutation-safety/README.md)：部分完成（partial，原项目）。既有安全回滚与 prune 有本地真实 PG 证据，原完整官方差分、规模性能与发布预演未齐；保留历史状态，新存储治理在独立项目推进。

- [Thread 到期与可靠删除生命周期](projects/20261030-thread-retention-lifecycle/README.md)：`done`（7/7任务、7条Phase与Final核验通过）；post48双包正式发布、四产物hash/60源码匹配、无缓存冷安装及正式13项通过，使用方正式锁与三服务/浏览器验收完成。旧post45不能接管014；已验停维护保留任务、恢复匹配post48续跑，不承诺恢复已删数据。未提交Git或部署现役。

- [Run 接受回执与只读对账](projects/20261010-run-acceptance-receipts/README.md)：`done`（13/13）；源码/候选与正式PyPI wheel/sdist各5组真实进程通过，正式四产物独立hash匹配、使用方服务锁/部署断言已接入，发布后notify/Stop/撤权联合验收完成，平台B01解除。平台T08剩余矩阵、T10/F12、新Linux应用镜像及现役启用另验。

- [Thread metadata 条件更新](projects/20261009-thread-metadata-cas/README.md)：partial；C01/C02 源码与候选完成；post44 已包含 CAS，C03 文档中的发布阻塞描述需该专项据此复核，使用方正式源接入未由 webhook 专项验收。独立 URI 避免旧服务忽略条件；实际 PG 并发、v2 冷安装与真实受管标题链路通过，不改现役。

- [Run completion webhook](projects/20261009-run-completion-webhooks/README.md)：partial；post44通用webhook/原子终态/可靠dispatcher/容量/积压/回退/发布完成，正式最短通过；native尾段定时/回填完成，队列等待超时且重启未执行。G4正式完整矩阵资源blocked，等用户通知后本地复验；前端由使用方专项验收。

- [Thread 固定目标取消](projects/20261007-thread-cancellation/README.md)：blocked（独立专项状态保留）；原子目标/固定回执/迁移011已包含正式post44，发布事实已补记。原post43候选回归/冷安装、16条使用方HTTP/Docker及7条恢复证据保留；使用方正式源锁与完整接入验收仍由其专项复核，现役未升级。

- [Worker 取消传播与 LangGraph Server 对齐](projects/20261007-worker-cancel-propagation/README.md)：done；13个Task/Phase与Final通过，源码270/Cron3/发布包24，四产物发布/PyPI哈希/新安装通过。两版Python SDK各24及包回退/kill/混合12、性能安全、平台默认0.123秒/9项子图通过。取消/lease/停止经验已获用户批准写入 [runtime-persistence](lessons/runtime-persistence.md)，前端交接已补齐；运行超时专项匹配回退/G07后续完成，模型可靠性专项前端/完整回退/Final另验，现役未升级。

- [Worker attempt 时间预算](projects/20261006-run-timeout-budget/README.md)：done；G05-G07、正式post42四产物/使用方锁定/冷安装、12组HTTP/SSE、新PID/新H接管与匹配旧Runtime/post41回退通过，无Schema迁移。历史高负载失败保留；使用方全量4项既有失败已旧源码对照，平台整体partial仅剩前端与联合Final，现役未升级。

- [Cron 契约对齐](projects/20261005-cron-parity/README.md)：本地 done，双包 post41 已发布 PyPI；锁定 SDK 的创建、到期 Run、`cron_id` 关联、暂停/删除、DST、截止时间、故障重试、重启、生产签名与租户隔离已在独立 PostgreSQL/Redis 验证。post41 补有限耗尽、编辑身份、通用历史分页、Thread 并发创建与删除后 queued Run 失败终态；CI 126 passed/4 skipped，PyPI 独立安装通过。平台实时撤权在应用层实现，发布包隔离链路已通过，生产平台未部署。

- [流缓存与回放资源治理](projects/20261005-stream-resource-bounds/README.md)：双包 post39 已发布，PyPI 独立安装/CLI 验证通过；平台现役 Runtime 已升级重启，多会话浏览器验收通过，done。

- [Worker 图级优雅停机发版](projects/20261004-worker-graph-drain-release/README.md)：`done`；`0.13.0.post38` 双包已在 PyPI 发布并独立安装验证。
- [Worker 图级优雅停机](projects/20261004-worker-graph-drain/README.md)：`done`；RunControl drain、同 Run checkpoint 续跑及隔离 PostgreSQL/Redis 链路验收通过。
- [子智能体 Checkpoint Namespace 历史持久化](projects/20260928-subagent-checkpoint-namespace-history/README.md)：`done`；双包 post37 升级并放通 `checkpoint_ns`，子智能体工具调用历史 100% 可查。
- [SSE 事件流保活心跳与连接容错](projects/20260927-sse-stream-heartbeat-and-resilience/README.md)：`done`；网关保活心跳注入与前端容错对齐。
- [Runtime 业务边界解耦与脱敏](projects/20260925-runtime-business-boundary-decoupling/README.md)：`partial`；通用 Agent Server 边界治理，消除存量业务字段。
- [Runtime 事件保留与清理](projects/20260925-runtime-event-retention/README.md)：`done`；优化事件存储周期与重放性能。
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
