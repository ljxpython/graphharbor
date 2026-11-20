# Worker 取消传播与 LangGraph Server 对齐 - 任务拆分

## 进度与完成条件

当前状态：**done，全部任务与Final完成**。用户批准的实施、跨包/平台依赖验证、直接PyPI发布及新安装复验完成；Final源码270/Cron3/发布包矩阵24通过，四产物哈希一致。平台前端、完整平台升级/回退及超时专项G07保持独立待验边界。

| 阶段 | 状态 | 完成条件 |
|---|---|---|
| Phase 0 基线与契约 | `[x]` | 官方/候选各 24 项 HTTP/Python/JS 对照、默认失败基线与停止证据冻结 |
| Phase 1 Runtime Worker | `[x]` | control listener、执行清理、续租/代际、rollback 与故障定向通过 |
| Phase 2 Langhost | `[x]` | cancel/wait/SSE/join、批量/Threadless 与超时/鉴权通过 |
| Phase 3 跨包联调 | `[x]` | 默认平台 Worker、9 项实际子图与跨实例/故障/进程 kill 通过 |
| Phase 4 发布门禁 | `[x]` | 双包锁步、回退、性能/安全、文档同步、直接发布及PyPI复验完成 |

## 本轮文档交付

### [x] P00：记录设计决定与跨仓库分工

- **改动内容：** 核对当前 Worker/控制队列/取消入口及锁定官方源码，记录用户决定，建立跨包任务和验证映射；同步平台取消决策、V01-C 与前端交接。
- **代码位置：** 本项目四份文档；`docs/CONTEXT.md`、`docs/FEATURES.md`、`docs/compatibility/profile.md`；使用方 ai-agent-platform 仓库的 `docs/projects/20261006-agent-model-resilience/`。
- **预期结果：** 规划可直接用于后续实施，官方证据边界、受理/停止差别及未实施状态清楚可查。
- **验证项：** D01 文档检查；源码阅读和既有证据核对不等于 HTTP 复测、功能 Phase 或 Final 通过。
- **状态：** `[x]` 2026-10-07 文档已建立；实施与发布追加授权已记录，功能进度如下。
- **合规检查：** [x] 文档交付；[x] D01 已执行；[x] tasks 状态更新；[x] CONTEXT/FEATURES 同步；CHANGELOG 跳过（规划本身为 docs）。

## Phase 0：基线与契约

### Task 0.1：冻结官方 Worker 取消对照

- **改动内容：** 在已读源码基础上，用隔离官方服务和 GraphHarbor 冻结 `wait=false/true`、interrupt/rollback 的码、body、headers、异常与完成顺序；明确使用何种持久依据区分 Worker 停止和 reaper fence。
- **代码位置：** 本机锁定包 `langgraph_api/worker.py`、`api/runs.py`、`asyncio.py` 和 `langgraph_runtime_inmem/ops.py`；现有 `scripts/compare_official_protocol.py`；本项目 `plan.md`。
- **预期结果：** 官方源码的 false=202、当前 GraphHarbor=200 差异有运行证据；提前 terminal 的缺口明确，停止确认在重启后仍可区分。优先现有 schema，确需扩展时先评审影响。
- **验证项：** E02/E03 → 官方与候选各 24 场景通过；false 空 body 202，true 200，body 兼容差异已冻结。复用事件表保存停止依据及 rollback receipt，无 Schema 迁移。
- **状态：** `[x]` 已完成 2026-10-07；见 `evidence/cancel-http-sdk-protocol.json`。
- **合规检查：** [x] 契约冻结；[x] HTTP/SDK 验证执行；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

### Task 0.2：建立取消观察指标和默认失败基线

- **改动内容：** 新验收记录意图提交/ACK、Worker 收到、execution 和资源退出、terminal 提交、lease 释放、等待响应完成；平台另记录候选调用数。保留旧默认失败与 1 秒对照，不用放宽断言替代即时通知测试。
- **代码位置：** GraphHarbor 的 `libs/langgraph-runtime-pg/tests/test_production_contract.py`；使用方 ai-agent-platform 仓库的 `apps/runtime-service/tests/integration/test_model_resilience_worker.py` 与 `docs/projects/20261006-agent-model-resilience/evidence/`。
- **预期结果：** 7.925 秒和 0.705 秒仍是优化前基线；新测试用 barrier 证明 heartbeat 首个 tick 前已收到控制通知，并核对清理/确认顺序。
- **验证项：** I01/I04、P01 → 24 样本、并发 8、首个 heartbeat 前取消、零失败；控制接收/退出/提交分段延迟和资源回收见 `evidence/cancel-performance.json`。旧默认失败证据保留，平台新 ACK→释放 0.123 秒且仅 primary。
- **状态：** `[x]` 已完成 2026-10-07。
- **合规检查：** [x] 指标与基线完成；[x] 屏障/采样已执行；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

## Phase 1：Runtime Worker

### Task 1.1：接入 Run 级 control listener

- **改动内容：** claim 后、factory 前复用 control queue；确认共享 mux 订阅就绪后复查 control key/PG，重连后复查活跃 Run。识别既有 Core JSON 与 ops 字节 action，done/未知消息不能误取消；Redis 故障保留可观测的 PG 兜底。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` → `ProductionWorker.run_once()`、`_cancel_requested()`；`redis_stream.py` → `StreamManager.add_control_queue()`、`aget_control_key()`、`_pubsub_mux_loop()`、`remove_control_queue()`；必要时核对 `ops.py:listen_for_cancellation()`。
- **预期结果：** 默认 heartbeat 不负责即时唤醒；先取消后监听、注册中取消和重连窗口不丢意图。复用共享订阅，Run 完成回收 queue/listener，不为每个 Run 新建 Redis 连接。
- **验证项：** U01、I01~I03 → `test_registration_window_does_not_start_factory`、`test_cross_instance_subscription_and_reconnect_recheck`、`test_real_pubsub_disconnect_rechecks_cancel_key`、控制发布失败 PG 兜底与 action 隔离通过。共享 mux 两条连接，退出后控制队列归零。
- **状态：** `[x]` 已完成 2026-10-07。
- **合规检查：** [x] 实现完成；[x] 定向验证通过；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

### Task 1.2：取消通用执行并等待资源收尾

- **改动内容：** 控制通知参与 factory/graph 执行等待，取消 execution Task 后 await 流、可跟踪子任务、上下文和 event buffer 收尾。重复取消不打断清理；HITL、shutdown drain、timeout 和 lease 丢失保留各自原因。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` → `ProductionWorker.run_once()` 的 execution/cancellation/shutdown 等待与 `finally`；`graph_executor.py:invoke_graph()` 仅在透传缺口存在时改动。
- **预期结果：** GraphHarbor 管理的异步执行退出后才确认停止，不吞原生取消或将其归入 infrastructure retry；核心不加入模型候选/供应商逻辑。同步线程或不响应清理不能伪报已停止。
- **验证项：** U02、I04/I06/I09：factory、节点、stream、子图和慢清理的确定性图；无 orphan task/连接；使用方模型计数由 Task 3.1 验收。
- **状态：** `[x]` 已完成 2026-10-07；factory/node/stream/受管子任务及真实嵌套子图、慢清理与重复通知屏障通过，见 verification.md 的 Runtime 定向回归与 HTTP Phase。
- **合规检查：**
  - [x] 代码实现完成
  - [x] 验证项已执行，最终定向 129 passed/4 skipped，HTTP/SDK 与实际子图停止屏障通过；skip 不计通过
  - [x] tasks.md 状态已更新
  - [x] CONTEXT.md 已更新
  - [x] docs/FEATURES.md 已更新
  - [x] docs/CHANGELOG.md 已更新

### Task 1.3：保护清理租约并原子确认终态

- **改动内容：** 修正公开 interrupted 后 renew 被拒绝的清理窗口；按 owner + lease generation 续租和确认，清理期间保持 Thread 保护。执行/资源退出后事务性更新 Thread、lease 与唯一 terminal，再发布完成通知。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py` → `RunRepository.renew()`、`finish()`、`record_event()`；`production_worker.py` → `_heartbeat()`、`RunCancelled` 与 `finally`；checkpoint fence 按需核对。
- **预期结果：** SSE/join/cancel wait 共享持久停止依据；running 受理阶段不发停止 terminal。旧代际写入拒绝，同 Thread 下个 Run 不在清理未完成时领取。
- **验证项：** U03、I04/I06/I08 → 慢清理续租/Thread 队列、同 owner 旧 generation、取消/完成竞态、提交失败重试、API 事务失败不发布、单 terminal 通过。
- **状态：** `[x]` 已完成 2026-10-07。
- **合规检查：** [x] 实现完成；[x] 定向验证通过；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

### Task 1.4：rollback 与崩溃恢复保持真实确认边界

- **改动内容：** 正常 rollback 等执行停止后恢复 checkpoint 基线并清理 Run；清理失败持久重试。reaper 对 lease 到期的取消 Run 做 fence/收尾，禁止重新执行；恢复结果与 Worker 停止确认区分。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py` → `rollback_run()`、`complete_rollbacks()`；`run_store.py:requeue_expired()`；`production_worker.py:reap_once()`；必要时核对 `ops.py:Runs.sweep()`。
- **预期结果：** pending rollback 不删除既有 Thread 历史；running rollback 恢复事务提交后才能确认成功。lease 到期或 Run 不存在本身不构成执行停止证据，已取消 Run 不因 crash 或 shutdown requeue。
- **验证项：** U03、I05/I07/I08 → rollback 基线保护、恢复失败持久重试、stale writer/fence、真实 Worker kill 三阶段 × interrupt/rollback 通过。缺少退出证据时 503，已取消 Run 未重执行；receipt 可跨恢复回查。
- **状态：** `[x]` 已完成 2026-10-07；见 `evidence/cancel-release-rollback.json`。
- **合规检查：** [x] 实现完成；[x] 定向及真实进程验证通过；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

## Phase 2：Langhost 契约

### Task 2.1：修正 cancel API 的受理与等待语义

- **改动内容：** 复用 interrupt/rollback 参数；running 受理保留 lease 且不生成停止 terminal。wait 先建立观察再提交取消，等待 Task 1.3 的持久停止依据；覆盖 rollback 恢复确认、超时/断开和自然完成。
- **代码位置：** `libs/langhost/src/langhost/core_api.py` → `_cancel_row()`、`runs_cancel()`、`runs_cancel_many()`、`_wait_for_run()`；join/wait 调用方共同核对。
- **预期结果：** false 仅受理，true 的成功晚于清理/确认；清理超时、fence-only 和授权失败不返回成功确认。响应按 Task 0.1 冻结的锁定官方表面和明确兼容差异处理。
- **验证项：** U04、I04/I05、E02/E03 → 24 项 HTTP/Python/JS、wait 清理屏障、超时/断开意图保留、rollback、授权拒绝通过。false=202；true 等持久停止，无法确认=503；confirmed body 为 null 的差异写入 Profile。
- **状态：** `[x]` 已完成 2026-10-07。
- **合规检查：** [x] 实现完成；[x] 契约/SDK 验证通过；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

### Task 2.2：补齐控制发布和 SSE 结束边界

- **改动内容：** 统一单个/批量、Thread/threadless、interrupt/rollback 的 after-commit control 发布，核对内部 ops/现有 disconnect 取消入口；SSE/join 在持久停止确认后结束，不以提前状态关闭流。
- **代码位置：** `libs/langhost/src/langhost/core_api.py` → `_cancel_row()` 及调用方；`streaming.py` → `_run_sse()`、`thread_stream()`；必要时核对 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py` 既有入口。
- **预期结果：** PG 取消提交后通知失败可恢复，回滚的 API 事务不投递控制；Threadless 同样收到通知。terminal 与 done 顺序一致，SSE 断线恢复不重复执行。
- **验证项：** U01/U04、I03/I05、E03/E04 → 提交后发布、Threadless/rollback/批量、SSE/join 慢清理与回放、跨实例 control 通过；既有断开策略回归通过。
- **状态：** `[x]` 已完成 2026-10-07。
- **合规检查：** [x] 实现完成；[x] 契约/流回归通过；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

## Phase 3：跨包联调

### Task 3.1：Platform API 与 Runtime adapter 对齐

- **改动内容：** 核对 `wait`、`action`、错误和超时在 Platform API → GraphHarbor SDK/HTTP → Worker 链路中完整透传；模型 middleware 收到取消后不访问 fallback。
- **代码位置：** **ai-agent-platform 仓库**：`apps/platform-api/src/platform_api/adapters/langgraph/runs_sdk_adapter.py:LangGraphRunsSdkAdapter.cancel()`、`runtime_gateway_upstream.py`；`apps/runtime-service/tests/integration/test_model_resilience_worker.py`、durable tests；平台专项 Task V01-C。
- **预期结果：** 平台停止操作与直接 GraphHarbor SDK 语义一致；不增加前端配置和第二套 Run 状态机。
- **验证项：** E01/E02/E03 → 平台真实 HTTP adapter 默认配置取消通过，ACK/确认/最终都只有 primary；DearFlow general-purpose、Showcase research/general-purpose × provider/backoff/cooldown 共 9 passed，wait 阻塞到子图清理结束且候选数不再增加。GraphHarbor 工厂/stream 和等待超时由通用用例覆盖；Anthropic 真实 API 排除。
- **状态：** `[x]` 已完成 2026-10-07；平台 Task V01-C 已同步，原失败保留为基线。
- **合规检查：** [x] 适配与验收完成；[x] 平台/SDK 验证通过；[x] tasks 更新；[x] CONTEXT/FEATURES 同步；[x] CHANGELOG 取消修复已记录。

### Task 3.2：故障、竞态和重启联调

- **改动内容：** 验证 Redis PubSub 断线、API/Worker 重启、Worker kill、取消与自然完成/停机 drain 竞态、多 Worker 同 Thread 和 pending cancel。
- **代码位置：** `libs/langgraph-runtime-pg/tests/test_production_contract.py`、`test_checkpoint_mutation_safety.py`、`libs/langhost/tests/`；使用方平台的 durable/integration tests。
- **预期结果：** 取消意图不丢、不重复执行；旧 Worker 只作为回退行为，不能被计入即时取消通过。
- **验证项：** I01~I09、E04 → 129 项定向、跨实例与真实 PubSub 重连、PG 兜底、终态提交/rollback 恢复故障、wait 超时/断开及进程 kill 通过；取消后未重执行，缺少退出证据为未确认。完整源代码回归 270 passed，专用 Cron 隔离回归 3 passed。
- **状态：** `[x]` 已完成 2026-10-07；故障不计入健康即时通知采样。
- **合规检查：** [x] 场景完成；[x] 故障/竞态已执行；[x] tasks 更新；[x] CONTEXT/FEATURES/CHANGELOG 同步。

## Phase 4：发布门禁

### Task 4.1：双包锁步、回退和兼容性验证

- **改动内容：** 统一确定候选版本，构建双包同版本 wheel/sdist，冷安装执行协议/SDK/Worker 矩阵；演练 post41 回退和 active Run 对账。通过后按用户授权从本机凭据直接发布，再从 PyPI 独立安装核验。
- **代码位置：** `libs/langhost/pyproject.toml`、`libs/langgraph-runtime-pg/pyproject.toml`、`uv.lock`、`scripts/check_versions.py`、`scripts/check_compatibility_baseline.py`、`docs/compatibility/profile.md`。
- **预期结果：** 生产包不依赖 `langgraph-api`，双包版本一致；新旧版本回退有明确未覆盖窗口和数据处理方式。
- **验证项：** R01/R02、P01/S01 与 verification.md 中的定向/全量命令；独立 PG/Redis、多实例、双包冷安装和回退演练。fixture 同样会清库，不能用现役数据库；安全隔离是命令执行前提。
- **状态：** `[x]` 已完成 2026-10-07；post42 runtime→CLI共四产物直接发布，PyPI哈希与本地一致；Python3.13新环境CLI/79项依赖通过，锁定与安装版Python SDK/HTTP/JS各24项及发布包回退/kill/混合12项通过。
- **合规检查：** [x] 发布/安装完成；[x] 冷安装/回退/性能安全/PyPI复验通过；[x] tasks更新；[x] CONTEXT/FEATURES/CHANGELOG及发布说明同步；未提交/部署现役。

### Task 4.2：完成项目文档与发布评审

- **改动内容：** 更新 FEATURES/CONTEXT、兼容性 Profile、受影响 REST/SSE 标准、runbook、CHANGELOG 和平台交接，记录 Phase/Final 分离结果与用户实施/发布授权。
- **代码位置：** 本项目 `README.md`、`plan.md`、`tasks.md`、`verification.md` 及受影响规范。
- **预期结果：** 只有所有关键链路和回退门禁通过后才能将本项目标为 done；未通过时保留 partial/blocked 和证据。
- **验证项：** D01 本地链接/源码位置/任务映射/状态核对；既有 `scripts/check_versions.py` 与兼容基线检查；人工发布评审记录。
- **状态：** `[x]` 已完成 2026-10-07；标准/Profile/runbook、两仓状态/交接、实现/发布证据与Final已同步；D01文档checker24份、专项/发布说明14份/37引用、空白/Task映射通过。用户实施/发布授权已记录，Final在全部完成卡完成后执行。
- **文档收尾补充：** 用户明确同意经验落笔，已补 `docs/lessons/runtime-persistence.md` 与索引/CONTEXT；平台详细前端任务书包含网关响应映射、源码任务和停止验收，交同事实装。本次只验文档，不新增发布或功能 Final。
- **合规检查：** [x] 文档/授权记录完成；[x] D01检查通过；[x] tasks更新；[x] CONTEXT/FEATURES/CHANGELOG同步；平台整项和其他超时专项保持真实未完成边界。

## 进度追踪

- [x] P00 文档规划与跨仓库交接
- [x] Phase 0 基线与契约
- [x] Phase 1 Runtime Worker
- [x] Phase 2 Langhost 契约
- [x] Phase 3 跨包联调
- [x] Phase 4 发布门禁
