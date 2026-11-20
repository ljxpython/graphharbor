# 任务与验证

> 2026-10-09发布事实补记：post44正式双包已包含CAS，发布条件解除，见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。C03仍需独立使用方正式源接入验收，状态保持blocked；以下post43/candidate记录为历史事实。

## [x] C01 数据库通用条件更新
- 改动：共享 JSONB 原子 merge/CAS，ops 和公开 HTTP 共用；授权过滤与条件同一 UPDATE。
- 位置：`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/thread_metadata.py`、`ops.py::Threads.patch()`、`libs/langhost/src/langhost/core_api.py::threads_update()`。
- 结果：条件失败 409；不丢失并发 metadata；不引入业务逻辑或数据库迁移。
- 验证：隔离 PostgreSQL 两种写顺序、同值标记失效、并发合并、不可见资源和 HTTP 参数拒绝。
- 状态：2026-10-09完成；4项真实PG/HTTP通过（36.35s）；见verification.md。
- 合规：[x]实现；[x]Phase；[x]任务；[x]CONTEXT；[x]FEATURES；[x]CHANGELOG。

## [x] C02 候选安装与使用方链路
- 改动：独立临时目录构建/安装候选双包，验证真实 HTTP 与使用方 transport。
- 验证：记录双包来源、哈希与 HTTP 契约；候选不冒充 PyPI 正式能力。
- 状态：2026-10-09完成；v2双包wheel/sdist、冻结依赖cold install与使用方2项/9场景通过，真实受管模型含CAS；正式post43实查不含CAS。
- 合规：[x]实现；[x]Phase；[x]任务；[x]CONTEXT；[x]FEATURES；[x]CHANGELOG。

## [ ] C03 正式双包发布和使用方正式源接入
- 状态：blocked；本轮不发布，需要正式发布指令与唯一新双包版本，使用方自动开关保持关闭。已有PyPI post43不能覆盖。

## Phase 验证记录

见[verification.md](verification.md)。C01/C02完成，C03独立block；Final未执行。
