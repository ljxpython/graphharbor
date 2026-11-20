# 验证

下方早期 Phase 保留当时发现的缺口；当前结论以末尾 post41 Final 为准。post40 已发布，post41 为平台接入后补齐的现行版本。

## Phase 证据（2026-10-05）

独立库 `graphharbor_cron_probe_20261005`（迁移 `010_run_queue_position`），独立 Redis 前缀 `graphharbor:cron-probe-20261005`；现役库未清理。`pytest -q -s /tmp/test_graphharbor_cron_probe.py`：显式启动官方 scheduler 后 `1 passed in 3.08s`，SDK create/search/update(enabled=false)/delete 与到期 pending Run 成功。此前仅启动 GraphHarbor 生命周期等待 30 秒未生成 Run；显式派发后的 Run `kwargs` 无 `cron_id`，worker 终态未验。探针输出有缺 `config` 的身份提取警告。

## Phase 任务对照

- T0：隔离库基线探针，见下方首个 Phase 证据。
- T1：官方 `langgraph-api==0.13.0` 调度行为核对；生产身份测试通过。
- T2：SDK CRUD、搜索投影与参数校验测试通过。
- T3：双调度器、失败回滚和应用重启测试通过。
- T4：签名、跨租户和凭据撤销测试通过。
- T5：隔离库 E2E、DST、`end_time` 与 worker 终态测试通过。
- T6：post40 双包正式发布、PyPI 独立安装与发布门禁通过，历史结果见首轮 Final。
- T7：post41 通用契约、双包发布、独立安装和平台发布包隔离链路通过，见下方独立 Phase 与末尾 Final。

## Phase 证据（隔离库 worker 终态，2026-10-05）

执行 `DATABASE_URI=postgresql+asyncpg://lijiaxin@127.0.0.1:5432/graphharbor_cron_probe_20261005 REDIS_URI=redis://localhost:6379/0 GRAPHHARBOR_REDIS_PREFIX=graphharbor:cron-probe-20261005 .venv/bin/pytest -q libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py`：`1 passed`。官方 SDK 创建、计数、更新暂停、搜索删除；REST 读取；两个调度器并发只领取一次；`keep` 和默认 `delete` 模式均生成成功 Run，Run 元数据可按 `cron_id` 查询。`keep` 保留线程及状态，默认模式未留下线程。修改文件 `ruff check` 通过。该测试会清空指定隔离库，严禁指向现役库。

仍未证明完整 API 差分、时区/DST、`end_time`、调度故障回滚和重启恢复；生产身份及撤权策略未完成安全验收。本节是 Phase，不是 Final。

## Phase 证据（自有调度器，2026-10-05）

在独立库 `graphharbor_cron_probe_20261005`、独立 Redis 前缀 `graphharbor:cron-probe-20261005` 执行更新后的 `/tmp/test_graphharbor_cron_probe.py`：`1 passed in 3.05s`。GraphHarbor 生命周期自动派发到期 cron，生成 `pending` Run；`kwargs.config.configurable.cron_id` 与 cron ID 一致；SDK 创建/搜索/暂停/删除通过。示例凭据 `postgres/postgres` 首次连接失败，改用本地测试用户后通过。未验证 worker 终态、双实例、故障恢复和鉴权；本结果不是 Final。

- [x] 官方 SDK/REST 参数、时区、DST、end_time、on_run_completed 差分（所列场景）。
- [x] 单实例与双实例到期、重启恢复、派发故障、不重复领取。
- [x] 后台身份、跨租户隔离、撤权后行为与敏感字段保护。
- [x] worker success 终态和 Run/cron_id 关联；通用 worker 的 timeout、取消和 HITL 中断契约另有 3 个用例通过，未新建 cron 专项 failed/interrupted 用例。
- [x] Final 契约、隔离库 E2E 和恢复演练；结果见下方独立记录。

## Phase 证据（T7 post41 平台接入补齐，2026-10-05）

有限 cron 的最后一次派发保留 Run，耗尽后停用；编辑执行 payload 刷新已认证身份，启停不覆盖身份；通用 Run metadata 查询先过滤再 SQL 分页；同 Thread ID 并发创建通过 PostgreSQL 原子唯一约束收敛；Thread 已删除的 queued Run 进入 error/thread_not_found 并保留 terminal event，不永久 pending。未引入平台业务表、角色或非标准公开 cron API。

- CI 七文件与 cron live：126 passed、4 skipped、4 warnings，41.85 秒，日志 `/tmp/graphharbor-post41-tests.log`。跳过项不计为通过。
- 版本检查、`uv lock --check`、Ruff check/format、mypy 通过；双包 post41 各构建 wheel/sdist 并正式上传 PyPI。
- PyPI 两包版本 JSON HTTP 200；独立 PyPI 安装的 CLI/runtime 均为 post41，源码来自 site-packages。
- 平台使用已发布包的真实 scheduler/ProductionWorker、独立 PostgreSQL/Redis、生产签名和应用授权链路，22 个 Run：8 success、14 预期 error；`cron_id` 关联、暂停/删除、once、并发 manual、撤权和审批失败断言通过。日志 `/tmp/scheduled-chain-published-verification.log`；确定性图替代模型调用，未调用生产 LLM。

## Final 验证记录（post40，2026-10-05）

**环境：** PostgreSQL `graphharbor_cron_probe_20261005`、Redis 前缀 `graphharbor:cron-probe-20261005`。测试会清空此隔离库；未接触现役库。版本锁定 `langgraph-api==0.13.0`、`langgraph-sdk==0.4.3`。

**执行命令与结果：**

- `DATABASE_URI=postgresql+asyncpg://lijiaxin@127.0.0.1:5432/graphharbor_cron_probe_20261005 REDIS_URI=redis://localhost:6379/0 GRAPHHARBOR_REDIS_PREFIX=graphharbor:cron-probe-20261005 .venv/bin/pytest -q libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py libs/langgraph-runtime-pg/tests/test_official_sdk_contract.py libs/langgraph-runtime-pg/tests/test_rest_contract.py`：10 passed，2 条 LangChain v3 streaming beta warning。
- 同环境执行上述三个文件及 `test_production_contract.py`：78 passed、4 skipped、2 failed。失败为旧测试预期 Alembic head `009_event_retention_watermarks`、实际已是 `010_run_queue_position`，以及独立重跑亦失败的非 cron Run 取消后 Lease 未清除断言。两项均未由本次 cron 代码改动引入，不计作通过；须由相应模块另行修复。
- 相关授权/REST 子集：15 passed、1 skipped、61 deselected。
- 通用 worker timeout、取消和 HITL 中断三个定向用例：3 passed。
- `ruff check`、`ruff format --check`（四个改动 Python 文件）与 `git diff --check`：通过。

**行为证据：** SDK 创建、搜索/计数/更新、暂停/删除；双派发器只领取一次；Run `metadata` 和 `configurable` 带 `cron_id`；worker 成功终态；默认与 `keep` 模式；纽约 DST 转换；创建/更新 `end_time`；派发失败不推进触发时间并可重试；应用重启后恢复派发；生产身份按 Run 签名，跨租户 cron 不可读改搜，撤销 API 凭据后旧 cron 仍运行。测试只覆盖锁定版本与所列场景，不代表所有 LangGraph API 行为已穷举。

**补充验证（2026-10-05）：** 双包 `0.13.0.post40` 完成 wheel/sdist 上传；PyPI 两包版本 JSON 均返回 HTTP 200，从 PyPI 独立安装后 `graphharbor --version` 输出 `0.13.0.post40`。`uv lock --check`、版本检查、Ruff check/format、mypy 通过；隔离库 cron 契约 77 passed/4 skipped，生产契约 122 passed/4 skipped，事件留存独立库 8 passed。首次合并运行的外围失败已修正测试夹具并分库重跑通过。未执行生产部署或回退演练。

**四态判定：** GraphHarbor cron 实现与 PyPI 发布为 `done`。平台业务侧的实时撤权与项目归属是另一个专项，不能从本项目的生产身份签名测试推断已完成。回退方式为停用调度器并暂停/删除 cron；未执行生产部署或生产回退。

## Final 验证记录（post41，2026-10-05）

**结论：** T0—T7 全部完成，GraphHarbor cron 契约和双包 PyPI 发布 `done`。锁定 `langgraph-api==0.13.0` / `langgraph-sdk==0.4.3` 的既定场景已验证；不声称穷举整个 LangGraph API。早期 post40 的外围失败及后续夹具修正保留在上方历史记录；post41 当前测试范围为 126 passed、4 skipped。

**环境与复验命令：** 仅允许独立 PostgreSQL `graphharbor_cron_probe_20261005` 和 Redis 前缀 `graphharbor:cron-probe-20261005`；测试会清空隔离库，禁止指向现役库。下列命令对应本轮真实通过的测试范围：

```bash
DATABASE_URI="postgresql+asyncpg://lijiaxin@127.0.0.1:5432/graphharbor_cron_probe_20261005" REDIS_URI="redis://localhost:6379/0" GRAPHHARBOR_REDIS_PREFIX="graphharbor:cron-probe-20261005" uv run pytest -q \
  "libs/langhost/tests/test_official_protocol_compare.py" \
  "libs/langgraph-runtime-pg/tests/test_production_contract.py" \
  "libs/langgraph-runtime-pg/tests/test_persistence_contract.py" \
  "libs/langgraph-runtime-pg/tests/test_public_runtime.py" \
  "libs/langgraph-runtime-pg/tests/test_rest_contract.py" \
  "libs/langgraph-runtime-pg/tests/test_official_sdk_contract.py" \
  "libs/langhost/tests/test_cli.py" \
  "libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py"
```

**发布产物：** `dist-post41/` 中以下四产物已上传，PyPI JSON 中的 SHA256 与本地实际文件逐一相同：

| 文件 | SHA256 |
|---|---|
| graphharbor_runtime-0.13.0.post41-py3-none-any.whl | 83fa11b28d7846cbcebcc6b516ce549c38bf4bc9a3ff551635668445be6af0d0 |
| graphharbor_runtime-0.13.0.post41.tar.gz | 11f49391afe8441c9bd5fe15dc63508185f0b280a25c8b4e4b2cfaf8600d96de |
| graphharbor-0.13.0.post41-py3-none-any.whl | 765c5b393acee4326e53305ba55d2c72ff43ecc1d5470bef48ab37d996be56e2 |
| graphharbor-0.13.0.post41.tar.gz | 210817811b77d9ab6aa645cff39e435fa2f957c8c703c210034d862244174242 |

**消费方验证：** 平台 pyproject/uv.lock/.venv 实际同步 post41，发布包隔离链路 22 Run 通过。一次聚合授权、项目归属和业务拒绝审计属于 Platform API/Runtime；由应用专项证明，不混入 GraphHarbor 通用身份契约。平台全量回归的 3 个外围失败已经用 HEAD 源码复现，详见 ai-agent-platform 的定时任务 verification.md，不计为 GraphHarbor 测试通过项。

**状态与边界：** README/tasks/plan/CONTEXT/FEATURES/CHANGELOG 已同步 post41；8 个已完成任务与 8 条 Phase 对照一致，双库 diff --check 通过，专项及本次新增文档链接无断链。已获得本会话开发与直接发布授权，未 commit/push/tag，未部署或重启现役平台。平台前端、生产部署与生产回退不属于本库交付范围；独立库暂停/删除门禁通过。
