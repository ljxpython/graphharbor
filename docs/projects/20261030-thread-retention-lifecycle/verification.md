# Thread 到期与可靠删除验证

状态：已完成（done，7/7 任务）。验证日期：2026-10-30。未执行项不写通过，skip 不计通过，重叠套件不加总。

环境：本地独立 PostgreSQL/Redis、隔离虚拟环境；双包 `0.13.0.post48`。正式源无缓存冷安装使用 Python3.11.9。未接触生产数据，保留原有未提交改动。

## 验证计划

- 策略参数、分钟单位、固定与闲置到期、显式续期、普通读取不续期。
- 并发续期/提交/扫描、删除标记与 checkpoint 写入保护；活跃 Run、未确认 Lease、Cron 保护。
- 前后钩子失败、进程中断、任务恢复、幂等及缺配置拒绝跳过。
- Thread、Run、Cron、checkpoint、事件实际清理，接受回执/回调保留。
- 鉴权及跨租户；report 严格只读、有界批次与多实例。
- 全量源码回归、lint/format/type/版本/锁文件、候选 wheel/sdist。
- 四产物发布/哈希核对、PyPI 冷安装与公开接口实测、使用方隔离栈接入。
- 新增迁移及停维护回退验证，保留任务和墓碑。

## Phase 验证记录

### G01 2026-10-30
人工批准来自本次用户对话；已读取 AGENTS、CONTEXT、双包规范、发布规范、经验库。未执行数据删除。

### G02 2026-10-30

- 策略/Schema/API 最短验证：`libs/langgraph-runtime-pg/tests/test_thread_retention.py`、`test_schema_setup.py`、`test_rest_contract.py` 与官方 SDK 契约通过；fixed/idle、分钟单位、服务器时间、鉴权和普通读取不续期均覆盖。
- migration014 在既有数据上增量升级，不倒推存量到期；破坏性 downgrade 明确拒绝。正式 HTTP 探针的 `http-policy-touch`、`read-does-not-touch` 另有发布后证据。

### G03 2026-10-30

- `test_thread_retention.py` 的真实 PG 删除矩阵、touch/删除并发、Run/Lease/Cron保护、前后hook失败/缺配置、重启续跑与最小墓碑通过；checkpoint写入、复制、prune及Run提交屏障契约同步通过。
- 正式探针覆盖 `before-hook-retry`、`deleting-fence`、`after-hook-retry`、`restart-resume`、`completed-tombstone`、`no-uuid-resurrection` 和 `completed-metadata-minimized`，任务各阶段及数据清理断言通过。

### G04 2026-10-30

- `libs/langhost/tests/test_cli.py` 与生命周期最短套件通过；report只读、limit100/最大1000、Worker同入口、多进程claim和失败可查询均覆盖。
- 正式CLI `cli-preview-read-only` 通过；使用方两个维护进程并发仅推进一个持久任务，见使用方 `retention-stack.json`。

### G05 2026-10-30

源码完整回归命令（在本仓库根目录执行）：

```bash
uv run python scripts/run_isolated_tests.py .venv/bin/python -m pytest -q --tb=short --ignore=libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py
uv run python scripts/run_isolated_tests.py --database-name graphharbor_cron_probe_20261005 --redis-prefix graphharbor:cron-probe-20261005 .venv/bin/python -m pytest -q --tb=short libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py
```

- 实际：源码392 passed、22 skipped、4 warnings；固定独立资源Cron另3 passed。skip不记为通过。
- 首次未拆分运行：2 failed、393 passed、22 skipped，原因是Cron套件固定测试库/Redis前缀与通用隔离runner配置冲突。拆分并指定该套件资源后两组均退出0；保留首次失败事实，未弱化契约断言。
- `uv run ruff check .`、`uv run ruff format --check .`、mypy（45个源文件）、`uv lock --check` 与 `uv run python scripts/check_versions.py` 通过。旧版恢复探针加入后，`scripts/verify_thread_retention.py` 再次Ruff与format通过。

### G06 2026-10-30

- 本地构建post48双包wheel/sdist，候选wheel测试23 passed、sdist测试19 passed；依序使用本机仓库外凭据直接发布Runtime/CLI正式四产物。
- 本地四产物与正式源SHA256逐一相等，60个Python源码文件与发布wheel逐字节一致；未修改已发布包源码。哈希见 [发布说明](../../releases/0.13.0/release-notes-0.13.0.post48.md)。
- 本地证据：`.cache/retention-governance/dist-post48/`、`artifact-sha256-post48.json`、`pypi-sha256-post48.json`。未执行Git commit/push/tag或CI发布。

### G07 2026-10-30

正式源 `--no-cache` 独立安装后双包版本/CLI正常，真实HTTP、PG/Redis、故障重启及旧包恢复命令：

```bash
uv run python scripts/run_isolated_tests.py .cache/retention-governance/pypi-post48-venv/bin/python scripts/verify_thread_retention.py --cli .cache/retention-governance/pypi-post48-venv/bin/graphharbor --rollback-cli .cache/retention-governance/rollback-post45-venv/bin/graphharbor --output .cache/retention-governance/pypi-rollback-post48.json
```

- 实际退出0，13项断言通过，证据 `pypi-rollback-post48.json`（基础10项亦见 `pypi-http-post48.json`）。除上述10项外，`stopped-maintenance-preserves-task`、`old-schema-guard-rejects-live-downgrade`、`resume-matching-release` 通过。
- 旧post45接管014被严格schema head门禁拒绝，返回 `PostgreSQL schema contract is missing; run graphharbor migrate upgrade`。这是已验证的降级限制；恢复post48后原任务继续完成，不能以降包恢复文件或数据。
- 平台Runtime已锁定正式post48，所属API/Worker重启；专属三服务13检查、真实模型浏览器5检查通过，详情见 [使用方验证](../../../../ai-agent-platform/docs/projects/20261030-session-and-runtime-retention-governance/verification.md)。本项目不代替其他历史专项验收。

## Final 验证记录

### 2026-10-30 最终验收

前置核验已通过：7项任务勾选、7张完成卡、7条Phase与README/plan/CONTEXT均为“任务全部完成，待最终核验”后进入Final。Final复核完整回归、候选/正式包和使用方链路证据，并独立执行产物、配置及文档一致性检查；未把Phase改名为Final。发布包源码没有变化，未重复运行已通过的相同昂贵套件。

| 验证范围 | 实际结果与证据 |
|---|---|
| 完整源码与真实PG/Redis | 392 passed/22 skipped、独立Cron3 passed；策略、鉴权、REST/SDK/CLI、SSE、checkpoint/Run/租约、并发与可靠删除回归通过，命令及首次失败修正在G05 |
| 候选与正式包 | wheel23/sdist19 passed；正式无缓存冷安装、双包版本/CLI和13项真实HTTP/重启恢复通过，证据见G06/G07；不加总重叠场景 |
| 使用方 | 正式锁post48、三服务13检查、真实模型浏览器5检查通过；平台定义策略，Runtime执行资源hook，核心不包含应用规则 |
| 最终产物复核 | Python hashlib/zipfile断言：本地四产物与已核验PyPI清单相等，60个Python源文件与wheel逐字节相等；独立PyPI虚拟环境和平台Runtime实际安装的双包均为post48；输出 `.cache/retention-governance/final-artifact-review-post48.json` |
| 版本/锁 | 再执行 `uv run python scripts/check_versions.py`、`uv lock --check` 均退出0；双包post48、锁定依赖一致 |
| 文档与差异 | 两仓 `git diff --check` 通过；本仓14份相关Markdown的本地链接/新增锚点核验无新增断链。广扫发现1条HEAD既有断链，未改无关历史链接，详情 `final-doc-review-post48.json` |

回退实测按G07验收：旧post45不能接管014，破坏性downgrade拒绝；停维护保留任务、恢复匹配post48继续通过。post48没有独立TTL关闭开关，冻结需暂停相关写入口、Worker与apply CLI；已删内容无法靠降包恢复。

### 状态一致性与标准检查

- [x] README、plan、tasks、verification与CONTEXT均为done，7/7任务、7张完成卡、7条Phase一致。
- [x] FEATURES、CHANGELOG、发布说明、兼容增量与生产手册已同步。
- [x] REST契约既有active状态保留，last_verified刷新2026-10-30并加入本项目；不替其他历史专项解除门禁。
- [x] 经验沉淀建议随交付提出，未擅自修改既有lessons；原有未提交修改保留。

### 最终结论

**done**：本项目通用生命周期实现、本地打包正式发布、独立安装、真实持久化/恢复及使用方接入均已完成，无当前范围内未完成任务。

未执行Git commit/push/tag、生产部署、生产容量压测或销毁既有用户数据。使用方Linux systemd未实跑；远端失联恢复为受控fixture边界，不宣称真实离线节点验收。已验证的版本/迁移恢复限制见上。
