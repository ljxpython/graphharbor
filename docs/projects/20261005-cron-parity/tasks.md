# 任务

### T0 隔离库基线
- **改动内容：** 复现 SDK CRUD 可用、独立部署未启动调度器及缺少 `cron_id` 关联。
- **代码位置：** `docs/projects/20261005-cron-parity/verification.md`。
- **预期结果：** 差距有可复现证据。
- **验证项：** 隔离库 Phase 探针通过。
- **状态：** [x] 已完成 2026-10-05。

### T1 生命周期、身份与故障方案
- **改动内容：** 自有 PG 调度器；每次派发以创建时身份快照签名，权限撤销由资源所有方禁用或删除 cron。
- **代码位置：** `docs/projects/20261005-cron-parity/plan.md`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py`。
- **预期结果：** 不引入平台业务 ACL，行为与锁定 LangGraph 版本一致。
- **验证项：** 官方调度器源码差分、生产身份隔离测试通过。
- **状态：** [x] 用户授权实施，2026-10-05。

### T2 SDK/REST cron 契约
- **改动内容：** 校验表达式/时区/截止时间，更新触发时间，支持搜索排序/投影，保存 `cron_id`。
- **代码位置：** `libs/langhost/src/langhost/core_api.py` → `cron_create()`、`cron_update()`、`crons_search()`。
- **预期结果：** 锁定 SDK `0.4.3` 不改代码即可操作 cron。
- **验证项：** `test_cron_dispatch_live.py`、`test_official_sdk_contract.py` → 7 passed；加 REST 契约最终重跑 10 passed。
- **状态：** [x] 已完成 2026-10-05。

### T3 调度与恢复
- **改动内容：** 生命周期启动/取消调度器；PG 行锁领取、逐条事务回滚与重试，Run 持久化后入队。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py` → `dispatch_due_crons()`；`production.py` → `lifespan()`。
- **预期结果：** 双实例不重复领取，创建失败不推进时间，重启后继续派发。
- **验证项：** 隔离库并发、故障注入、应用重启测试通过。
- **状态：** [x] 已完成 2026-10-05。

### T4 身份与租户隔离
- **改动内容：** 保存受信任身份并为每次 Run 签发生产 token；按应用 Auth 元数据过滤 cron 资源。
- **代码位置：** `libs/langhost/src/langhost/core_api.py` → `cron_create()`；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py` → `dispatch_due_crons()`。
- **预期结果：** 跨租户无权读改删；撤销 API 凭据阻断新请求，既有 cron 继续以保存身份运行，须主动暂停/删除。
- **验证项：** 生产签名/跨租户/撤权隔离测试与授权回归通过。
- **状态：** [x] 已完成 2026-10-05。

### T5 隔离库 E2E 与文档
- **改动内容：** SDK 创建 → 到期 Run → `cron_id` 关联 → 暂停/删除；验证 `keep`/默认模式、DST、`end_time`、失败与重启。
- **代码位置：** `libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py`、`docs/compatibility/profile.md`、`docs/FEATURES.md`。
- **预期结果：** 隔离库验证可重复，兼容性边界可审阅。
- **验证项：** 隔离库与 REST 最终重跑 10 passed；授权/REST 子集 15 passed、1 skipped；worker 终态定向 3 passed；Ruff check/format 通过。扩展回归两项外围失败见 `verification.md`。
- **状态：** [x] 已完成 2026-10-05。

### T6 PyPI 正式发布
- **改动内容：** 双包锁步升至 `0.13.0.post40`，隔离构建 wheel/sdist，依次发布 runtime 和 CLI。
- **代码位置：** `libs/langhost/pyproject.toml`、`libs/langgraph-runtime-pg/pyproject.toml`、`uv.lock`、`scripts/check_versions.py`。
- **预期结果：** PyPI 独立安装版本一致。
- **验证项：** 版本、锁文件、Ruff、mypy、CI 生产契约 122 passed/4 skipped、cron 相关 77 passed/4 skipped、事件留存独立库 8 passed；PyPI 双包 JSON 200，独立 CLI 报 `0.13.0.post40`。
- **状态：** [x] 已完成 2026-10-05。

合规检查：实现、Phase、任务状态、CONTEXT、FEATURES、CHANGELOG 与 Final 已同步；未提交、推送或打 tag。

## 平台接入补齐

### T7 post41 通用契约补齐与正式发布

- **改动内容：** 有限 cron 耗尽保留最后 Run；执行 payload 编辑刷新可信身份，暂停不覆盖；通用 Run metadata SQL 分页；Thread 创建原子唯一；Thread 已删的 queued Run 保存 error/thread_not_found 及 terminal event。
- **代码位置：** libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py → dispatch_due_crons()；run_store.py → RunRepository.search()/claim_next()/record_event()；libs/langhost/src/langhost/core_api.py → cron_update()/threads_create()；双包 pyproject.toml、uv.lock、scripts/check_versions.py。
- **预期结果：** LangGraph 公开 cron API 保持兼容；应用可复用通用仓储，失败 Run 不永久 pending；无平台业务表、角色或非标准公开产品接口。
- **验证项：** CI 七文件与 cron live 合并 126 passed/4 skipped；Ruff check/format、mypy、版本与锁检查通过；wheel/sdist 构建并发布，PyPI 两包 JSON 200；独立 PyPI 安装 CLI/runtime 均 post41；平台发布包探针 22 Run（8 success、14 预期 error）通过。
- **状态：** [x] 已完成 2026-10-05；双包 0.13.0.post41 已发布，见 verification.md。
- **合规检查：**
  - [x] 实现与隔离验证完成
  - [x] tasks.md、README、方案与 Phase/Final 同步
  - [x] CONTEXT、FEATURES、CHANGELOG、兼容性边界同步
  - [x] 使用用户既有发布授权；未 commit/push/tag，未部署生产平台

## 进度

T0—T7 全部完成；当前双包 post41 已发布。平台权限复核由 ai-agent-platform 实现；其前端和生产部署不属于本库交付范围。初始 post40 验证证据保留在 verification.md。
