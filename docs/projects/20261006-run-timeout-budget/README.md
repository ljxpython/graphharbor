# Worker attempt 时间预算与官方对齐

- 状态：done；G01-G04保留历史，现行G05-G07及正式发布/使用方锁定/匹配回退门禁完成。平台整体仍partial，仅剩同事前端F01-F10与联合Final，不属于本通用底座实现范围。
- 批准：2026-10-06 用户在平台运行生命周期超时治理专项完成评审并明确授权实施。
- 修订：2026-10-07 用户明确要求按 LangGraph Server Worker 实现；原跨 attempt 总截止点取代为每次 claim 新预算。模型/provider TimeoutError 最终 error，Worker 硬限 timeout；数据库瞬时故障有界重试并从当前 Run checkpoint 继续。
- 范围：工厂/执行/drain 共享本 attempt 的 H；正常 checkpoint handoff 不占故障 attempt，租约 generation 继续递增；向 graph factory 提供私有快照，公开结果不泄漏。
- 不新增表、不加入平台业务字段、不修改原有cron工作。本专项原阶段未发布；2026-10-07用户另授权取消专项验证后直接发布，post42双包已上传，未提交或部署现役。
- 任务见 [tasks.md](tasks.md)，证据见 [verification.md](verification.md)。

平台方案：使用方仓库 `docs/projects/20261006-agent-run-timeout-governance/plan.md`。

锁定基线 `langgraph-api==0.13.0`，只覆盖Worker超时/重试/停机切片，不宣称全部Server drop-in。官方4项、PG/Redis116 passed/4 skipped及checkpoint补验通过；取消专项正式源码Final270 passed/8 skipped与Cron3通过，本专项复用该同版源码证据，未冒充重新执行。使用方正式post42冷安装/锁定、完整12组HTTP/SSE、真实PID `11475 -> 12285` 新attempt接管通过；匹配旧Runtime/post41的drain后回退、checkpoint/pending/历史/产物和普通/取消/HITL通过。先前高负载失败保留在Phase。证据见 [verification](verification.md) 与 [evidence](evidence/README.md)；现役平台仍旧版本，未升级、提交或重复上传。
