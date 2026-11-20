# 验证

## 当前事实更新（2026-10-07）

post42已随用户授权的[取消专项](../20261007-worker-cancel-propagation/verification.md)完成双包发布、四文件哈希核对与Python3.13独立PyPI安装；发布包取消HTTP/锁定及安装版Python SDK/JS矩阵各24项，回退/kill/混合12项通过。下面“未发布/404”为当时的Phase事实，原产物哈希不用于证明当前发布文件。

用户恢复后，G07正式包12组平台HTTP/SSE、新PID接管、使用方锁定/冷安装与匹配旧Runtime/post41回退通过，本专项done。平台整体partial仅剩前端F01-F10与联合Final；现役服务未升级。当前结果在下方最新Phase，历史高负载/404不代表当前状态。

## G07 使用方正式PyPI验收 Phase（2026-10-07恢复后）

复用取消专项已发布的四产物，PyPI metadata SHA256与发布清单一致；没有重新上传同版本候选文件。使用方Runtime pyproject/uv.lock及当前venv升级post42，只变两个GraphHarbor包及产物信息，其他依赖版本不漂移；冻结同步141项check、新独立Python3.13.9冷环境142项check通过。HTTP环境LangGraph1.2.11/SDK0.4.3/core1.6.0/SQLAlchemy2.0.52；API自身SDK0.4.2/Python3.14.6另验85项unittest通过。

- 使用方 `scripts/verify_run_timeout_budget.py` 二轮完整12组退出0，见 [正式包日志](evidence/run-budget-pypi-post42-http-e2e-r2.log)。覆盖short success、wrapup success、slow timeout、模型scope单attempt error、HITL新Run、三Thread、SSE恢复、cancel竞态、排队/G=0/防注入。首轮验收脚本误把原生202当平台ACK，按平台原有200修正后二轮通过，无公开响应改动。
- 真实SIGTERM/接管run `875b0bbd-3481-47ec-887f-c4638f589d1c`，PID `11475 -> 12285`；等旧H=30截止后，新attempt H=20/G=5从当前Run checkpoint继续，最终timeout。每个已验Run断言租约释放、durable终态恰好1条、公开预算不泄漏、完整checkpoint和已提交文件可读。
- slow run `8e8ad5e8-7611-4f12-8a95-28b40bae5759` timeout；model `4869ea23-d7be-48b5-8949-5de73d1d1441` error；HITL resume `13151343-fa3b-4461-b850-5f1b82a03531` success。SSE run `2db29999-595b-4ad8-923e-6bf9436dea22`回查/下一Run通过；故意断开连接有一次SQLAlchemy关闭CancelledError诊断，原日志保留，不冒充无诊断。
- 使用方匹配回退 `scripts/verify_run_timeout_rollback.py` 第六轮退出0，见 [JSON](evidence/run-budget-pypi-post42-rollback.json) / [日志](evidence/run-budget-pypi-post42-rollback-r6.log)。真实独立API/Worker、两版Reference Agent组合根/fake model/测试Auth；当前Runtime/post42先drain并暂停新提交，旧源码/post41接管同run `e9fd1e38-5d4c-4cf0-9e06-aea029d680b6`，prepare节点不重执行。pending `e3733be2-1b8e-4030-955d-8b059110bc4a`、历史/工作区保留；普通/取消/HITL及resume通过，无Schema migration。新Runtime不得只降依赖到post41。
- 所有资源均为独立实例和专用测试库/Redis前缀；HTTP使用 `agent_run_budget_e2e_20261006_e7dd` / `graphharbor:run-budget-e2e:e7dd`，匹配回退使用 `graphharbor_event_retention_verify` / `graphharbor:run-release:e7dd`，没有现役清库/重启。

使用方Runtime全量617 passed/2 failed/34 skipped/51 deselected，API全量326 passed/2 failed/16 skipped/654 subtests；两项PTY时序、两项HTML预览断言在旧Runtime源码同样复现。没有改无关实现或宣称全仓全绿；早期MCP/向量/安装定位环境问题已解除，skip/deselected不计通过。定向预算/主子/取消及停止字段投影另有通过证据，详见使用方专项verification。确定性模型只证明提示送达，不保证供应商服从或外部请求已撤销。

## 2026-10-07 修订 Phase

官方基线 `langgraph-api==0.13.0` 的 worker.py SHA256：`cd6775e2267d9bd4cb6fdc83fd0c133311dc7ccf1d7776e12ad335973f3a33cd`。

- G05：`scripts/verify_official_worker_timeout.py` 实际官方 worker 分支探针四项通过：provider 超时 error、硬超时 timeout、旧 deadline 后新 attempt 成功、attempt 4 拒绝。隔离 graph/DB sink，不能冒充官方生产分布式 E2E。
- G05/G06：真实 PG/Redis 预算/生产/持久化/checkpoint 变更安全/公开契约五集 `116 passed, 4 skipped, 4 warnings in 231.38s`，日志 `/tmp/run-budget-official-regression-r2.log`。专用库 `agent_run_budget_20261006_e7dd`，Redis 前缀 `graphharbor:budget:official:e7dd`；skip 为既有 SDK 夹具缺项，warning 为 v3 实验提示。
- G06：首节点已完成后第二节点注入数据库故障，调用顺序 `first, second, second`；六次正常 drain 归还 attempt，之后按配置 2/5 个故障 attempt 到上限 error，generation 递增且旧 owner 拒绝写入。无 Schema migration。
- 类型/静态：三个改动模块 mypy（follow-imports=skip）通过；六个源码/测试/官方探针 Ruff check 与 format check 通过。asyncpg 无类型桩采用窄 ignore；DBAPIError.orig=None 明确处理。
- 首轮唯一失败是旧 success-commit 夹具用普通 ConnectionError 模拟 DB，按官方分类改 psycopg OperationalError 后二轮通过；初期 0.35 秒夹具受本机初始化耗时影响，调为 2 秒仍验证 factory/执行/drain 共用 H。

- 后续指定 checkpoint 重试修正后：普通/指定 checkpoint 均查询最新 checkpoint 并验证属于当前 Run 后继续；两路径调用 `first, second, second`。包括重试预算/异常边界定向 `4 passed, 10 deselected, 2 warnings in 85.88s`，日志 `/tmp/run-budget-official-checkpoint-final.log`，与116集不相加。
- 本机高负载使原秒级状态夹具与首次HTTP短任务撞硬限，这些失败轮次不作为通过。状态测试改受控旧deadline/恢复H=60；HTTP schema预热后H=30/G=10，真实接管旧H=30后新H=20/G=5；独立慢factory/执行/drain硬限H=2保留。

最终修订双包位于 `/tmp/run-budget-post42-official-final-dist`，冷环境 `/tmp/run-budget-official-post42-venv`。G07 HTTP后续单列；同版本候选包按目录/hash区分。Runtime wheel SHA256 `066e3ee102817ac201576d7952e45621c6cbdd978e2fa9b62f3f28f33dffc6ae`，CLI `f0bdd00ba7361dbbb45803ab54a88aaab3d9e2b6625499d3fb243ffed40e57d7`；未发布。

- G07冷安装：最终候选模块逐字等于当前源码，API/runtime均post42；LangGraph1.2.11、SDK0.4.3、LangChain-core1.6.0、SQLAlchemy2.0.52确认。平台Runtime预算/收尾32 passed，API超时契约3项通过，均显式导入当前worktree。
- **G07平台HTTP环境blocked：** `/tmp/run-budget-official-http-e2e.log`（H=8）与 `/tmp/run-budget-official-http-e2e-r2.log`（schema预热，H=30）均退出1，普通short最终timeout；第二轮在打开图前已到期，run `a5f4f1b6-9b55-4d78-8f0c-8aa92e08b728`。本机load813-852，未完成12组/SSE/真实新PID接管，不写为通过。需低负载隔离PG/Redis复跑；不动其他用户进程/现役H，不用旧10-06结果替代。
- 两仓diff检查、本仓9份定向文档checker与项目链接通过；兼容profile既有一条失效链接另记，不改无关历史。当前CLI/runtime post42 PyPI两项再次404；未发布/接入。

## Phase 验证记录

下列 10-06 跨 attempt 总 deadline 与旧接管场景是已被取代的历史。与修订冲突的证据不用于 G05-G07；公开过滤和同 attempt 取消等未变化行为保留。

- G01：正式源码、PostgreSQL 本地连通性和现有 Worker 调用点已核对。
- G02：`libs/langgraph-runtime-pg/tests/test_run_budget.py` 真实PG/Redis定向16 passed；首次冻结、重试/reaper/shutdown保留预算，到期不打开图，不同Worker H不续期。
- G03：factory/执行/drain共用剩余时间；私有快照只供factory，公开Run/事件过滤。慢factory/模型、旧租约与终态竞态在组合测试覆盖。
- G04：预算/生产/持久化/公开契约100 passed、4 skipped、4 warnings；skip来自既有上游SDK夹具缺项，不算通过。专用库 `agent_run_budget_20261006_e7dd` 与Redis前缀 `graphharbor:budget:e7dd`，没有使用现役库。

双包本地产物 `/tmp/run-budget-post42-dist` 含wheel/sdist；候选冷环境 `/tmp/run-budget-post42-venv` 同时安装graphharbor与graphharbor-runtime 0.13.0.post42，`RunRepository`包含run_timeout_seconds。原post41冷环境确认无此参数，没有混跑旧/新Worker。

使用方 `scripts/verify_run_timeout_budget.py` 隔离HTTP验收退出码0；专用库 `agent_run_budget_e2e_20261006_e7dd`，Redis前缀 `graphharbor:run-budget-e2e:e7dd`，日志 `/tmp/run-budget-http-e2e.log`。12组结果覆盖普通/软收尾/硬超时/模型error/HITL新Run/三Thread/SSE恢复/取消竞态/排队/G=0/防注入。真实Worker重启run `e81d1534-bfbd-44f0-8df5-0c1e0c24f2b6` PID `60590 -> 62050`、原H=30秒/新配置H=600秒，原deadline不变，到期接管不再次构图；每Run durable终态恰好1条、租约释放、已提交文件与checkpoint可回查。

构建入口：`uv build --package graphharbor-runtime --out-dir /tmp/run-budget-post42-dist`、`uv build --package graphharbor --out-dir /tmp/run-budget-post42-dist`。未上传，PyPIpost42双包版本查询404；使用方正式锁仍post41。

## Final 验证记录

### 2026-10-07 验收归并

**前置条件：** G01-G04为历史阶段，现行G05-G07以及正式发布/使用方接入/匹配回退全部完成。以下如实复用同一正式版本已有Final和使用方新Phase，不宣称本专项再次运行取消专项全量或浏览器：

| 门禁 | 实际证据 |
| --- | --- |
| 同版源码Final | [取消专项Final](../20261007-worker-cancel-propagation/verification.md)：270 passed/8 skipped/13 deselected/4 warnings，独立Cron3 passed；覆盖生产/预算/恢复/取消，skip/deselected不计通过 |
| 正式包Final | 同专项PyPI HTTP/Python SDK/JS24场景、锁步/构建/49个Python源码一致、哈希/独立安装/CLI；本轮再次核对四产物metadata，无重复上传 |
| 官方Worker语义 | 本专项官方4项隔离分支、PG/Redis116项、checkpoint恢复补4项；探针不替代官方生产分布式差分 |
| 使用方G07 | 正式包完整12组平台HTTP与真实SIGTERM/新PID/新attempt，匹配旧源码/post41 API-Worker组合回退通过；原始证据已保存 |
| 静态与文档 | 本轮Ruff check/format覆盖70文件，三处uv lock --check与两仓diff检查通过；本仓11份/使用方16份定向文档checker及项目本地链接通过，状态一致 |

本轮最终读取正式冷安装包并逐字核对49个Python源码文件，见 [源码证据](evidence/pypi-source-match.json)；再次PyPI metadata四产物哈希等于发布清单，见 [核对证据](evidence/pypi-post42-verification.json)。本轮临时API/Worker/独立PG/Redis均已退出；共享现役Runtime来自其他worktree，安装版仍post41，本会话没有升级/重启。

**判定：done，范围为GraphHarbor通用Worker attempt专项。** 没有Schema迁移、业务模型字段或Git提交/推送/tag/现役部署。平台前端F01-F10与联合Final尚未完成，使用方全量4项既有失败已旧源码对照；不把本底座done当成整套平台done或全部Server等价。历史失败Phase保留，正式发布文件哈希以取消专项清单为准。
