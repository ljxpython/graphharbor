# Worker 取消传播与 LangGraph Server 对齐 - 验证计划与记录

## 当前结论

**状态：done，专项实施、必要验证与授权发布完成。** Final源码270 passed、专用Cron3 passed、PyPI发布包矩阵24项通过，类型/格式/版本/锁文件及文档门禁通过；四产物哈希一致。平台V01-C通过，前端和完整平台回退/升级不在本专项done范围，现役仍post41。

后续使用方运行超时专项已完成G07正式包12组HTTP及其匹配Runtime/post41回退，见 [Worker attempt专项](../20261006-run-timeout-budget/verification.md)。下方Final中的G07 partial/blocked为当时的历史判断；模型可靠性专项的前端/完整Final仍由其自身跟踪，不因本次超时回退证据自动完成。

本文件的验证结果必须区分：

- **受理：** API 已授权、取消意图已提交，通知已尝试发布；通知失败时仍有持久兜底。
- **收到：** Worker control listener 观测到 interrupt/rollback。
- **停止：** GraphHarbor 管理的 execution、graph stream、可跟踪子任务和资源上下文已退出；使用方另验 middleware/provider 上下文。
- **确认与释放：** owner/代际校验后的持久终态、Thread 和 lease 事务已提交，监听/heartbeat 回收，后续 Run 可按现有调度规则领取。

只有“停止”和“确认与释放”均完成，才能把 `wait=true` 的成功确认记为通过。提前 interrupted、lease 到期、reaper fence 或 Run 不存在不能单独证明停止。Anthropic 真实 API 不属于本项目验收范围。

## 验证环境约束

- 锁定官方对照：`langgraph-api==0.13.0`、`langgraph-runtime-inmem==0.33.0`、Python SDK `0.4.3`、JS SDK `1.9.28`、`langgraph==1.2.11`。
- GraphHarbor 双包使用同一候选版本；测试使用独立 PostgreSQL/Redis 数据库和命名空间。
- `scripts/test.sh` 会清库；直接 pytest 的 `pg_runtime` fixture 也会在 setup/teardown 调用 `truncate_all()`。执行前显式将 `DATABASE_URI`、`REDIS_URI`、`GRAPHHARBOR_REDIS_PREFIX` 指向独立环境，不使用默认 `.env` 的现役连接。
- provider 使用确定性受控 fake/HTTP fixture；真实模型只验证平台适配器链路，不用于证明取消时序。
- 不修改现役服务、生产配置或凭据；保留脱敏 run_id、owner/generation、时间点和请求计数。记录 Python/双包/SDK 版本和默认 heartbeat 实际值，避免把 fixture 内的短心跳误当默认配置。

## 用例与任务映射

| 验证编号 | 内容 | 对应任务 |
|---|---|---|
| D01 | 文档链接、源码位置、任务/验证与状态一致 | P00、4.2 |
| U01/U02/U03/U04 | 控制监听、执行取消、Repository/rollback、网关契约 | 1.1~1.4、2.1/2.2 |
| I01/I02/I03 | 默认通知、订阅窗口、Redis 故障 | 0.2、1.1、2.2、3.2 |
| I04/I05 | 停止确认、interrupt/rollback 全入口 | 1.2~1.4、2.1/2.2 |
| I06/I07/I08/I09 | 竞态、crash、清理续租与 fence、原因隔离 | 1.2~1.4、3.2 |
| E01/E02/E03/E04 | 平台、官方 Python/JS、分离多实例 | 0.1、3.1/3.2 |
| P01/S01/R01/R02 | 性能、安全、冷安装回退、混合窗口 | 4.1/4.2 |

P00 仅表示规划交付；下表结果均有后文 Phase 运行记录。发布后独立安装与 Final 不使用规划或源码阅读替代。

## 验证计划

| 编号 | 已执行的验收范围 | 结果与证据 |
|---|---|---|
| U01/I01/I02/I03 | 订阅就绪、注册窗口、重连复查、JSON/字节 action、默认取消、Redis 断连与发布失败 PG 兜底 | 通过；`test_worker_cancel_propagation.py`，故障兜底不计入健康即时采样 |
| U02/I04/I08 | factory/节点/stream/TaskGroup/真实子图、慢清理、重复取消、Thread 保护 | 通过；wait/join/SSE 阻塞到资源退出，lease 清理期间按代际续租 |
| U03/I05/I06/I07/I09 | rollback/checkpoint、提交与恢复失败、取消/完成/drain/timeout 竞态、stale writer/fence | 通过；真实 kill 三阶段；API lifespan/PG pool 恢复和数据库 shutdown 错误注入是组合证据，不冒称完整生产进程故障穷举 |
| U04/E02/E03 | pending/running × interrupt/rollback × wait=false/true 的 HTTP/Python/JS；wait 超时/断开与鉴权 | 官方/GraphHarbor 各 24 场景通过，SDK 不改代码；body 差异明确保留 |
| E01 | 平台 adapter 默认 heartbeat、handler/流式、backoff/cooldown 与实际 general-purpose/research 子图 | 默认仅 primary，ACK→释放 0.123 秒；实际子图 9 passed，清理 barrier 与请求次数不变 |
| E04 | API/Worker 独立进程、跨 StreamManager 通知、多 Worker/同 Thread、旧代际拒绝 | 通过；只有当前 owner/generation 可提交停止，跨实例与注册/重连不丢意图 |
| P01 | 24 样本并发 8；publish→receive→stop→commit 分段延迟、默认 heartbeat 与资源回收 | 零失败、零 heartbeat tick；共享 PubSub 两条，退出队列/受管任务为零，见性能 JSON |
| S01 | 未授权/跨 scope、错误 Thread/Run、未知 action、故障 fence；控制 payload 只含 action | 通过；auth/tenant 组合契约及安全出口检查，无模型/凭据业务字段 |
| R01/R02 | 同版 wheel/sdist 构建、冷安装、post41 回退、真实 kill 与混合版本 | 冷安装 24、回退/kill/混合 12 通过；无 Schema 迁移，生产无 langgraph-api |
| 发布复验 | PyPI 四文件哈希、新隔离安装、版本/CLI/取消矩阵 | 通过；两版Python SDK矩阵各24项，发布包回退/kill/混合12项 |
| D01 | 文档、本地链接、实现位置、任务/验证/状态一致性 | 发布事实同步及Task映射通过；Final记录后再做状态核对 |

已有生产/rollback/budget/retention 测试在原文件扩展，新增控制/停止/故障/性能/鉴权断言集中在 `libs/langgraph-runtime-pg/tests/test_worker_cancel_propagation.py`，没有增加第二套控制状态机。原计划采用的 SDK 和 HTTP 基线已冻结；不将 skip、reaper fence 或公开 interrupted 算作停止确认。

## 可复现命令

所有命令从 GraphHarbor 根执行，复用 `scripts/run_isolated_tests.py` 创建并回收独立 PG/Redis。Cron 用其专用隔离库和 prefix，不能用现役 `.env` 或通用测试库绕过保护断言。

```bash
.venv/bin/python "scripts/run_isolated_tests.py" .venv/bin/pytest -q -m "not e2e" --ignore="libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py" --tb=short
.venv/bin/python "scripts/run_isolated_tests.py" --database-name graphharbor_cron_probe_20261005 --redis-prefix graphharbor:cron-probe-20261005 .venv/bin/pytest -q "libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py" --tb=short
.venv/bin/python "scripts/run_isolated_tests.py" .venv/bin/python "scripts/verify_worker_cancel_protocol.py" --output /tmp/graphharbor-cancel-http-sdk.json
.venv/bin/python "scripts/run_isolated_tests.py" .venv/bin/python "scripts/verify_worker_cancel_release.py" --candidate /tmp/graphharbor-cancel-post42-cold/bin/graphharbor --legacy /tmp/graphharbor-cancel-post41-rollback/bin/graphharbor --output /tmp/graphharbor-cancel-release.json
uv lock --check
.venv/bin/python "scripts/check_versions.py"
.venv/bin/python "scripts/check_compatibility_baseline.py"
```

本机 3.11 Python 的 urllib 默认系统 CA 不完整；查询 PyPI 改用已安装的 httpx/certifi，保持 TLS 校验。凭据仅由 dotenv 解析并注入上传子进程，禁止回显或写入证据。

## 验证记录

### 2026-10-07 规划记录

- 已核对 GraphHarbor 当前 `ProductionWorker`、`StreamManager`、`RunRepository`、cancel API 和 SSE 实现。
- 已核对本机锁定 `langgraph-api==0.13.0` Worker 的 control queue、取消监听和 `wait` 调用方式。
- 用户确认采用即时控制通知、PG/Redis 兜底、Worker 停止确认方案。
- 代码、测试、双包构建和真实跨服务验收尚未开始。

### 2026-10-07 D01 文档检查

本区仅记录规划产物检查，不计为功能 Phase 或 Final。

| 检查 | 实际结果 |
|---|---|
| 两仓库分别执行 `git diff --check`；额外扫描新增未跟踪文档行尾空白 | 通过，避免普通 diff 未覆盖新项目目录 |
| 平台既有 `scripts/check_docs.py:check_file()` 和 self-check；GraphHarbor 文档由同一 checker 的 ROOT 指向该仓库后只读检查 | 两边各 7 份文档通过，未引入机器绝对路径或退役服务名；未在 GraphHarbor 新建 checker |
| 临时 Node 只读核对本地 Markdown 目标/锚点、任务结构和验证编号 | 14 份文档，185 个有效本地引用、3 个锚点；13 个 Task 四段式及状态/验证映射通过，无新增失效引用 |
| 对照源码与既有测试位置 | 18 个关键函数/类入口核对通过；验证表的 8 个可复用测试均存在，功能测试未运行 |
| 状态与证据边界 | P00 完成，Phase 0~4 待实施，Final 未执行；平台默认失败证据和 V01-C 阻塞保留；未改运行代码、版本或锁文件 |

检查同时发现 4 个既有失效引用，均不在本轮新增/修改行：平台 `docs/FEATURES.md` 的澄清修复链接（应有上级目录）及 deployment-guide/operator-handoff 两个旧 quickstart 链接；GraphHarbor `docs/compatibility/profile.md` 的事件保留项目链接（缺上级目录）。本轮记录而未改动，不能据此宣称全仓文档链接均已通过。

## Phase 验证记录

### Runtime 与网关早期定向回归（2026-10-07）

通过 `scripts/run_isolated_tests.py` 创建临时 PostgreSQL 14/Redis、空闲端口与独立命名空间，执行：

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/pytest -q libs/langgraph-runtime-pg/tests/test_worker_cancel_propagation.py libs/langgraph-runtime-pg/tests/test_production_contract.py libs/langgraph-runtime-pg/tests/test_checkpoint_mutation_safety.py libs/langgraph-runtime-pg/tests/test_run_budget.py libs/langgraph-runtime-pg/tests/test_event_retention.py --tb=short
```

结果：**120 passed / 4 skipped / 2 warnings，62.62 秒**。skipped 为尚未启动的独立 SDK HTTP 集成，不计通过；warnings 为 LangGraph v3 beta 提示。覆盖 factory/node/stream/TaskGroup 子任务、真实 LangGraph 子图、慢清理续租、wait/join/SSE 停止边界、注册窗口 interrupt/rollback、跨实例通知、旧代际拒绝、crash fence 不冒充停止、API 事务失败不发布通知；既有 timeout/drain/HITL/rollback 与事件保留回归通过。

`mypy`：**38 source files 通过**；Ruff check 与 format：**49 files 通过**；`scripts/check_versions.py`：双包候选 **0.13.0.post42** 锁步通过。此处为源码 Phase，不代表构建、发布或平台 V01-C 完成。

### 锁定官方早期真实 HTTP 对照（2026-10-07）

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_worker_cancel_protocol.py --output docs/projects/20261007-worker-cancel-propagation/evidence/cancel-http-protocol.json
```

官方 `langgraph dev` 与 GraphHarbor `serve` + 独立 `worker` 各 **8 场景通过**，覆盖 pending/running × interrupt/rollback × wait=false/true。GraphHarbor running `wait=true` 在文件 cleanup barrier 未释放时不返回，释放后返回；官方 Python SDK `runs.cancel(wait=true)` 两边均通过。

冻结表面：false = 空 body **202**；true = JSON **200** 且有 `Location`/`Content-Location`。**明确兼容差异**：GraphHarbor confirmed body 为 `null`；官方 pending 为 `{}`，running 为 `__error__`（UserInterrupt/UserRollback）。官方 SDK cancel 丢弃返回 body，平台仍须验证其 ACK 归一化。保留差异，不宣称所有响应完全相同。证据见 [HTTP 对照](evidence/cancel-http-protocol.json)。

以上为早期基线；后续扩展到两种 SDK、故障/性能/安全、冷安装/回退与平台 V01-C，结果如下。

### P00 验证 2026-10-07

设计与任务/验证映射已交付，规划 D01 两仓各 7 份通过；实施/发布由用户追加批准，原失败证据保持不变。

### Task 0.1 验证 2026-10-07

锁定官方与候选各 **24 场景通过**，包括 8 HTTP、8 Python、8 JS；命令为上方 protocol probe，证据 [cancel-http-sdk-protocol.json](evidence/cancel-http-sdk-protocol.json)。冻结 false=202、true=200 与 null/官方 body 差异，两种 SDK 均丢弃 cancel body。

### Task 0.2 验证 2026-10-07

原默认 ACK 后新增备用与 1 秒 heartbeat 对照保留。新通用采样首个 heartbeat 前收到控制，平台默认 ACK→lease 释放 **0.123 秒**且 ACK/停止确认/最终均只有 primary；不将此单样本延迟当固定 SLO。

### Task 1.1 验证 2026-10-07

定向最终 **129 passed / 4 skipped / 2 warnings，59.93 秒**。命令沿用早期定向的五文件；新增真实 PubSub 断连/key 复查、注册窗口、重连、共享监听回收与 PG 兜底通过，skip 不计通过。

### Task 1.2 验证 2026-10-07

factory/节点/stream/TaskGroup 和真实 LangGraph 子图、重复通知/慢清理 barrier 通过；wait/join/SSE 在资源退出前不完成。使用方实际子图另有 9 passed，受管范围和同步线程/外部请求边界明确。

### Task 1.3 验证 2026-10-07

慢清理续租、Thread 下个 Run 不提前领取、同 owner 旧 generation、单 terminal 与取消/完成竞态通过。`test_stop_confirmation_retries_commit_failure_with_lease` 注入提交失败，重试期间保护不释放，完成后唯一确认。

### Task 1.4 验证 2026-10-07

rollback 基线/迟到 writer/恢复失败持久重试、lease fence 不冒充停止通过；真实 Worker kill 在取消前、清理中、确认后 × interrupt/rollback 共 6 项通过。没有退出证据时 **503**且未重执行；rollback 已删除后重复 cancel 为 404，receipt 可回查。

### Task 2.1 验证 2026-10-07

HTTP/Python/JS 矩阵与 slow cleanup、wait timeout/disconnect、pending/running/Threadless/授权拒绝通过；503 和断开后取消意图保留，200 不能早于持久停止。响应 headers/body 见协议 JSON，不把 Run interrupted 独自算确认。

### Task 2.2 验证 2026-10-07

API 事务回滚不发布、提交后 interrupt/rollback/批量/Threadless 通知通过；SSE/join 清理屏障、重放与既有断开策略回归通过。PG 持久确认优先于 fanout，通知失败仍能回查。

### Task 3.1 验证 2026-10-07

平台 **V01-C 通过**：默认 Worker 真实 HTTP adapter 一项；DearFlow general-purpose、Showcase research/general-purpose × provider/backoff/cooldown **9 passed，22.82 秒**。子图 barrier 未释放时 wait 阻塞，确认后候选次数不增长、子图清理完成、terminal.execution_stopped=true、lease 释放。普通组合回归 **12 passed / 9 skipped**，opt-in 子图另行实跑，skip 不算通过。证据在使用方平台 `evidence/cancel-default-post42.json` 与 `cancel-actual-children-post42.json`。

### Task 3.2 验证 2026-10-07

源码全量（非 E2E，Cron 单独）**270 passed / 8 skipped / 13 deselected / 4 warnings，87.50 秒**；专用Cron **3 passed / 2 warnings，8.03 秒**。API lifespan/PG pool恢复、PG shutdown注入、真实PubSub断连与Worker kill组合覆盖持久恢复；8 skipped和13 deselected均未计通过，取消专用HTTP/SDK另已实跑。

早期将 Cron 放进通用隔离库的全量运行有 **2 failed**（保护断言），其余 **271 passed / 8 skipped / 13 deselected**。未放宽保护；改用上方专用库/prefix 命令后 **3 passed**。保留原失败，不将环境保护说成已通过。

### 发布前构建、回退、性能与安全（Task 4.1 Phase）

- post42 四个 wheel/sdist 构建、双包冷安装/CLI、生产无 langgraph-api、49 个 wheel Python 文件与源码逐字一致通过；冷安装 protocol **24 场景通过**，见 [cancel-cold-sdk-protocol.json](evidence/cancel-cold-sdk-protocol.json)。
- 回退/真实 kill/混合版本 **12 场景通过**，见 [cancel-release-rollback.json](evidence/cancel-release-rollback.json)。drain 后 post41 接管 pending；新 API/旧 Worker 清理前 503，旧 API/新 Worker interrupt 提前 200；混合版本不计新版保证。
- 性能 **24 样本、并发 8、零失败、零 heartbeat tick**：publish→receive p50/p95 **17.443/34.076 ms**，receive→stop **5.358/8.967 ms**，stop→commit **152.454/341.860 ms**。PubSub 连接 **2→2**、退出控制队列/受管 Task **0/0**，见 [cancel-performance.json](evidence/cancel-performance.json)。没有新增延迟 SLO。
- auth/tenant/错误 Thread 与 action 拒绝、故障 fence/未确认契约通过；payload 与公开错误无凭据、模型配置或输入正文。mypy **38 source files**、Ruff check、format **83 files**、lock/version/compatibility 门禁通过。发布包/PyPI 复验待执行。

### Task 4.1 验证 2026-10-07

先runtime后CLI从授权本机配置临时读取令牌上传，共4文件；PyPI metadata/simple index与[哈希清单](evidence/release-artifacts.json)一致。Python3.13.9新环境从PyPI安装同版双包，CLI post42、79项依赖check通过，无langgraph-api。初次安装simple index尚未同步而失败，索引就绪后重试成功，未重传产物。

发布包使用锁定Python SDK0.4.3/JS1.9.28矩阵 **24通过**，见[PyPI矩阵](evidence/cancel-pypi-sdk-protocol.json)；同一包使用新环境解析到的Python SDK0.4.6再验 **24通过**，见[安装版SDK矩阵](evidence/cancel-pypi-current-sdk-protocol.json)。发布包回退/kill/混合 **12通过**，见[PyPI回退](evidence/cancel-pypi-release-rollback.json)。probe SDK标签改用实际metadata版本，避免0.4.6证据误标0.4.3；不影响已发布runtime源码。发布与现役版本见[pypi-release.json](evidence/pypi-release.json)。

### Task 4.2 验证 2026-10-07

发布前两仓checker **23份文档通过**，专项本地链接 **13份/30个**，已有受影响文档新增链接 **32个通过**；两仓git diff --check通过。13个Task完成卡四段式/状态核对，13个专属Phase记录（P00+12 Task）齐全，早期额外回归单独保留。REST标准已为active，只刷新事实/日期，不替平台未验SSE草案毕业。发布授权来自用户，未自行批准；无Git操作、现役升级，剩余平台前端/回退和超时G07限制明确。

## Final 验证记录

### 2026-10-07 Final

**前置检查：** 13个Task完成卡均完成，13个专属Phase记录齐全；执行前README/plan/tasks/CONTEXT均为“全部任务完成，Final验证中”。没有把早期Phase改记为Final。以下为前置检查之后实际执行的Final：

| 检查 | 实际结果 |
|---|---|
| 非E2E全量，独立PG/Redis，Cron单列 | **270 passed / 8 skipped / 13 deselected / 4 warnings，79.30秒**；skip/deselected不计通过，warnings为LangGraph v3 beta |
| 专用Cron库/prefix回归 | **3 passed / 2 warnings，10.75秒**；保护断言保持，未用现役数据库 |
| Python3.13 PyPI发布包HTTP/Python SDK0.4.6/JS1.9.28 | **24场景通过**；API/Worker分进程，慢清理/停止确认、interrupt/rollback、pending/running和SDK join/stream覆盖，见[Final矩阵](evidence/cancel-final-pypi-protocol.json) |
| Ruff check / format | 通过，**83 files**已格式化 |
| mypy | **38 source files**通过；既有deepagents override未用note，无类型错误 |
| uv lock / versions / compatibility | 通过，双包post42锁步，官方基线和精确依赖一致 |
| 文档checker与本地引用 | 两仓**24份文档**通过；专项/发布说明**14份、37个本地引用**通过；新增变更链接32个已通过，两仓diff/新增文档空白通过 |

Final全量包含控制订阅/代际/fence/慢清理/鉴权和并发资源回收用例；真实Worker kill、post41回退与混合窗口另以Task4.1的发布包12项证据验收，不宣称本区重新跑了该probe。平台默认0.123秒/9项实际子图、官方与候选各24项以及性能采样保留在Phase，不用它们替代平台浏览器/完整回退。

**发布事实：** runtime后CLI，共4个wheel/sdist，PyPISHA-256与本地清单一致；Python3.13.9独立安装、CLI与79项依赖check通过，无生产langgraph-api。原simple index同步失败已在就绪后成功重试；未再次上传或改变版本。没有Git提交/推送/tag，也没有现役升级/重启。

**最终判定：done。** Worker取消专项的实施、故障/协议/平台依赖联验、性能/安全、包回退、文档与授权发布均完成。已知body差异和混合版本未确认行为属于已冻结兼容边界；缺少真实退出证据时503是预期行为。平台模型专项的前端/完整平台回退/Final仍blocked，Worker attempt专项G07仍partial/blocked，不宣称整套LangGraph Server等价或现役平台已上线。
