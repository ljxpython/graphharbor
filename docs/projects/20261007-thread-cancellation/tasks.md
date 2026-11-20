# Thread 固定目标取消 - 任务

> 2026-10-09发布事实补记：post44正式双包已包含原语/migration011，见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。Task 4的发布前历史记录保留，发布条件已由post44解除；使用方正式源接入验收仍待本专项确认，整体blocked状态不变。

> 批准来源：2026-10-07 用户明确批准平台取消治理方案及全部非前端开发项。整体blocked：唯一post43发布准备已完成，正式PyPI上传等待明确指令，使用方正式源锁随后接入；进度以本文件为准，不以其他post42专项状态代替。

### Task 1：固定目标与路由

- **改动内容：** 原子完整活动快照、scoped key、持久回执、100项分页与精确回执授权事件。
- **代码位置：** `libs/langhost/src/langhost/cancellation.py::{cancel_active,get_cancellation,_receipt}`、`server.py::create_app`、`models.py::RunCancellationRow`、迁移011。
- **预期结果：** 冲突整单回滚，同key固定目标，新Run不误伤，平台属性不进入引擎。
- **验证项：** 151目标/分页、20次submit/claim竞争、重复/冲突/新Run测试通过。
- **状态：** [x] done，2026-10-07（源码与隔离证据）。
- **合规检查：** [x] 实现；[x] 验证；[x] 进度；[x] CONTEXT/FEATURES/CHANGELOG同步。

### Task 2：退出证明与Runtime身份

- **改动内容：** 回执要求无lease与持久退出事件；缺少证据返回confirmation_unavailable；传播服务端图身份。
- **代码位置：** `cancellation.py::_receipt`、`graph_executor.py::thread_config`；复用 `production_worker.py/run_store.py` 既有退出协议。
- **预期结果：** ACK/status/fence不冒充退出，主子图Runtime重建保留assistant_id/graph_id。
- **验证项：** 48项取消/Worker/public-runtime定向与平台真实四图、长模型/工具、双子Agent通过；lease fence无事件保持未知。
- **状态：** [x] done，2026-10-07。
- **合规检查：** [x] 实现；[x] 验证；[x] 进度；[x] CONTEXT/FEATURES/CHANGELOG同步。

### Task 3：容量、候选与回退

- **改动内容：** 1/10/100/500目标容量，双包wheel/sdist构建、独立冷安装、迁移备份/恢复与旧post41回退。
- **代码位置：** `libs/langgraph-runtime-pg/tests/test_thread_cancellation.py`、使用方 `scripts/verify_stop_migrations.py`。
- **预期结果：** 完整目标不截断，超时可回查原ID，回退不反向恢复已取消Run。
- **验证项：** 26项竞争/容量、唯一post43四产物import/head011/CLI/twine、主回归297项/Cron3项、Python3.12契约124项/3.13冷安装150项通过，使用方16条包版HTTP/真实Docker与7条恢复通过；500目标33.24秒/5524 SQL，线性成本已标注。skip不记为通过。
- **状态：** [x] done，2026-10-07（候选证据）。
- **合规检查：** [x] 实现；[x] 验证；[x] 进度；[x] 文档同步；无已批准SLO，不冒称低延迟达标。

### Task 4：正式发布与使用方接入

- **改动内容：** 唯一post43双包锁步正式发布、正式源独立安装与使用方依赖锁接入；现役部署另行指定。
- **代码位置：** 双包pyproject/锁/发布流程；使用方Runtime依赖。
- **预期结果：** 正式产物包含新接口/迁移，不能以旧同名post42代替。
- **验证项：** 双包pyproject/uv.lock/check_versions/兼容矩阵已同步post43；四产物哈希、uv lock --check、Ruff check/format、mypy、twine check与包版隔离验证完成。PyPI最新仍为post42，post43未占用；使用方临时锁接入仅改双包，仓库正式锁仍post41。
- **状态：** [ ] blocked，2026-10-07。发布准备完成，正式PyPI上传待明确指令；随后正式源独立安装并更新使用方锁/复验。现役部署另行指定。
- **合规检查：** [x] 发布准备；[x] 发布前验证；[x] 进度与阻塞记录；[x] CONTEXT/FEATURES/CHANGELOG同步；[ ] 正式源接入。

## 进度

- [x] 固定目标/分页/精确授权事件/迁移源码
- [x] 隔离竞争/停止证明/四图与容量
- [x] 候选冷安装/备份/回退/静态门禁与文档
- [ ] 本专项正式新版本/使用方锁接入（blocked）
- [ ] 整体Final（发布配套门禁未满足）
