# 方案

> 当前done：正式post42、使用方锁定/冷安装/12组HTTP与匹配源码回退已验收。下方原方案只保留历史语义；当前设计以2026-10-07修订为准，实际结果见verification。

## 2026-10-07 用户修订：对齐 LangGraph Server Worker

用户明确要求 GraphHarbor 按 LangGraph Server 的能力实现。本节取代下文原首次 claim 的持久总期限设计；原阶段证据保留，但不作为修订后验收结果。

- 基线：官方 `langgraph-api==0.13.0` 的 `worker.py:worker`，每个 attempt 用 `asyncio.wait_for(..., BG_JOB_TIMEOUT_SECS)`；默认 86400 秒。重试/重启/停机重新领取时生成新 attempt 预算，排队和退避不累计到它。Worker 硬限触发 `timeout`，不重试。
- 图内/provider 的 `TimeoutError` 是用户执行错误，最终 `error`；有界 Worker 重试只处理受支持的瞬时数据库错误，模型重试沿用 graph 自身 policy。
- `BG_JOB_MAX_RETRIES` 按锁定 Worker 的 attempt 判断执行，默认最大 3 个执行 attempt；支持显式配置，不把文档中的“重试 3 次”误写成固定 4 次。
- 停机宽限 `BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS` 默认 180 秒、最大 3600。checkpoint drain/handoff 不占故障重试次数；租约 generation 继续递增，复用现有 `retry_counters` 记录独立 attempt 数，不增加 Schema。
- 保留私有 factory 预算桥接软收尾，每次 claim 更新 UTC/monotonic 快照；这不是官方公开 API。主/子 Agent 在本 attempt 内共享预算，API/history/SSE 仍不公开。
- `GRAPHHARBOR_RUN_TIMEOUT_SECONDS` / `GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS` 作为已有配置别名继续可用；未设置时采用官方变量/默认值。不增加跨 attempt 总期限开关。
- 验证：官方 Worker 的隔离契约探针、真实 PG/Redis attempt 更新与重试次数、重复 drain 代次隔离、候选 wheel 冷安装，以及平台 HTTP/SSE/真实进程交接。

参考：[官方环境配置](https://docs.langchain.com/langsmith/env-var-self-hosted)、[恢复机制](https://docs.langchain.com/langsmith/scalability-and-resilience)、[handoff 更新记录](https://docs.langchain.com/langsmith/agent-server-changelog)。本切片通过不代表全部 Server 已兼容。

## 原方案（2026-10-06，已由上述修订取代）

历史状态（原实施时）：本地候选包完成，正式发布/使用方接入/回退尚未验收；后续修订和当前done结果见上方及verification，不用本节原跨attempt总限指导现行实现。

批准日期：2026-10-06，评审人：用户 @lijiaxin，沿用平台已批准方案。

复用 Run 行锁事务，在 `kwargs.__graphharbor_run_budget` 保存 version=1、started_at、deadline_at、timeout_seconds。数据库 clock_timestamp 为跨进程时钟源；Worker 查询数据库剩余秒数后转换为本进程 monotonic deadline。首次 pending 不计预算；重试/退避/停机计入；新 Run 独立。

Worker 在合并 Assistant/Thread/Run 配置后移除伪造预算，只向 factory 的 configurable 最后注入真实快照（额外包含 run_id、thread_id、deadline_monotonic）。invoke_graph 不携带快照，Run 序列化过滤私有 kwargs/config。硬限耗尽走原 running -> timeout 与租约校验；停止和 HITL 沿用 interrupted 及原 reason。

匹配回退：暂停新提交、drain/checkpoint交接后，同时切换使用方旧Runtime源码与post41双包；新Runtime要求私有预算，不能只降依赖或混跑并承诺新版语义。已在独立PG/Redis完成API/Worker组合回退，同Run从checkpoint继续、pending/历史/工作区保留，普通/取消/HITL通过；无Schema迁移。结果见verification新Phase。
