# Checkpoint 增长、回滚日志与无引用 blob 回收方案

启动日期：2026-11-20。级别：治理改动。用户明确要求独立立项；本项目规划通用 checkpoint 存储增长、回滚日志和 blob 回收，任务唯一来源为 [tasks.md](tasks.md) 的 S01–S07。原安全治理任务与测试留在原项目。

## 1. 目标与启动时事实基线

使用方持续运行同一 Thread 时，GraphHarbor 应保存可靠恢复与安全回滚所需的数据，避免每个新 Run 复制整条 Thread 的历史。使用方显式裁剪恢复点后，不再被任何存活状态引用的 blob 应可回收，必要的 Delta 祖先、pending writes、审批和回滚依据必须保留。

启动时的post48 `capture_baseline()`将Thread的全部checkpoints/writes及完整投影存入`run_checkpoint_baselines`；首次claim复制基线，历史越长，复制和加载成本越大。Delta不能消除这层重复。post49已按第4节替换为最小边界与撤销日志。

启动时的`prune_checkpoints()`保留namespace head和Delta必要祖先，但未回收blob，且把历史checkpoint/blob键全量加载到Python。2026-11-20仅有源码与内存桩证据；2026-11-21已完成真实PG容量、RSS与故障验证，结果见verification，仍不据此归因任何现役OOM。

## 2. 责任红线

GraphHarbor 是通用 Agent Server。本次核心能力只认识 Thread、Run、checkpoint、namespace、channel/version、pending writes、通用维护动作与运行租约。

- 不导入使用方服务，不依赖其源码、数据库、私有配置或文件目录才能运行。
- 不按特定 Agent、模型供应商、工具、角色、资料用途、评测来源或用户查看行为分支。
- 不硬编码使用方保留天数，不生成业务摘要，不裁剪消息正文，不重写使用方 reducer。
- 不把业务字段加入通用表、协议、CLI 参数或指标标签；生命周期资源仍通过既有通用钩子交给使用方。
- 可支持合法 Delta 与非 Delta channel；由图声明 channel 类型，引擎只处理对应存储契约，不强制整个生态使用 Delta。
- GraphHarbor 的独立验收只用确定性通用图和隔离数据库。业务模型和用户界面测试由使用方执行。

使用方接入与业务决定见[平台方案](../../../../ai-agent-platform/docs/projects/20261120-checkpoint-storage-governance/plan.md)。跨仓链接仅为文档导航，不成为代码、测试或发布依赖。

## 3. 授权与当前阶段

2026-11-20用户同意规划基线放大及blob回收修复，并明确核心保持纯通用、无需旧实现兼容、必要时可直接正式发布GraphHarbor。2026-11-21用户批准全部实施，并另行确认反向投影差量与绝对验收预算；S01–S07已完成，正式post49已发布。

用户已明确授权本专项验证所需的双包正式发布。通过源码与候选门禁后，按 [release-process.md](../../standards/release-process.md) 从 `~/.my_best/.env` 临时读取发布令牌，锁步发布新的唯一版本并独立冷安装复验；不需要重复索取相同发包授权。不得输出、复制或持久化令牌。规划阶段不读取秘密。

最初授权不包含GraphHarbor Git操作；2026-11-21用户另行批准两仓源码提交推送，覆盖正式post49与必要前置能力。平台使用方已要求在其独立 Worktree 开发，全部功能与必要验收完成后迁回本地 main，由其 P02/P07 记录；两仓保持独立交付边界。包发布和源码推送均不能代替现役切换或既有数据销毁授权。工作树原有大量修改，实施时已记录相关基线，构建和提交前均审查实际范围，不能夹带未经核实的变更。

旧版本精确行为兼容不再是本轮实现目标；原测试证据仍作为历史事实保留。当前安全回滚、并发隔离、已声明 API 语义和数据完整性属于功能正确性，不能用“不兼容”省略。

## 4. 单一目标实现

### 4.1 冻结依赖和表布局

实施先核对目标 LangGraph、checkpoint、checkpoint-postgres 版本的实际 `aput/aput_writes`、Delta sentinel、snapshot/counter、任务写入主键与更新行为。选择一组经真实存储验证的精确依赖，不写宽版本探测后 fallback，也不接私有实现的多个年代分支。

官方记录涉及早于LangGraph1.2.13的Delta/update_state问题。本次固定LangGraph1.2.24、checkpoint4.3.2、checkpoint-postgres3.2.1、SDK0.4.14、CLI0.4.31；实际验证Delta/update_state/fork、缺counter头及prune后继续执行，不能仅凭版本升级认定正确。

### 4.2 替代完整历史基线

采用“Run 起始边界 + 必要的撤销日志”作为目标设计，不引入新的调度或通用事件总线：

1. 首次 claim 只保存该 Run 开始时各 namespace 的 head/引用边界和必要的小型控制信息；不读取并复制整条 Thread 的 checkpoints/writes。
2. 对本次新增的 checkpoint/write，以真实事务中的键和执行归属记录可精确撤销的范围，不能按时间或 checkpoint ID 大小猜测 Run 归属。
3. 对本次可能改写的既有键，保存首次变更前的 before-image；同 Run 对同键重复更新只记录一次原值。不存在的原行用明确标记表示，不把缺失当作空值。
4. 撤销日志与对应 saver 写入处于同一 PostgreSQL 事务，并继承既有 run → thread 锁顺序、owner/generation/lease 屏障。不得先提交写入再异步补日志。
5. `ThreadRow.values_` 不再每Run复制完整历史。按2026-11-21用户批准采用标准JSON Patch反向差量，与结果投影同事务保存、与行撤销同事务恢复；不在锁内加载使用方graph/reducer。大范围实际替换的撤销量仍可能很大。
6. rollback 按日志恢复本 Run 改写的行、移除本 Run 新增且无外部依赖的数据，恢复投影并最后删除 Run；保持主子 namespace、pending writes、审批任务和回执一致。
7. 有后继依赖时继续明确拒绝不安全回滚；重试、重启、过期执行者不能绕过屏障。历史 Run 按当前允许的逆序回滚能力验收，不直接删除所有旧基线来缩小成本。

设计冻结前须用真实 saver 证明哪些表是仅新增、哪些键会 upsert；只有确认需要的 before-image 才进入日志。DDL、索引和迁移编号在 S02/S03 核对当前 head 后确定，不预留无法解释用途的表或字段。

回滚只执行已记录的行撤销与JSON Patch，不调用使用方构图或外部I/O；保持“恢复失败不删Run、可重试”的不变量，不增加旧实现回退路径。

### 4.3 安全裁剪与 blob GC

继续选择“保留必要祖先”，本轮不为了裁剪强行物化自定义 channel 快照：

1. 在获准且已锁定的 Thread 范围内执行，沿用活跃 Run、pending rollback 和删除屏障检查；所有公开与内部维护入口走同一存储服务。
2. 明确本次操作的根集合：各 namespace 当前 head、Delta 恢复所需快照、pending writes，以及仍有效的回滚/恢复引用。对于显式 `keep_latest` 按已声明语义失效的历史回滚窗口，必须先明确其失效边界再释放引用。
3. 逐 channel 沿真实 parent 链寻找可用 seed，处理 snapshot/counter 和非 Delta 的版本 blob 引用；不能把祖先计数器、行存在或时间先后等同于完整状态。
4. 在同一受保护数据库视图内计算存活 checkpoint、writes 与 `(thread_id, checkpoint_ns, channel, version)` 引用，再删除明确不再需要的 checkpoint/write/blob。与新日志有关的仍有效引用也必须计入。
5. 使用受限 SQL/分页和批量删除，不把全历史 checkpoint 或所有 blob 键加载进 Python，也不逐行循环提交海量 DELETE。
6. 每批次都保持存活状态可恢复；多批之间必须有稳定维护屏障或重新验证依赖，不能只在首批校验、后续裸删。失败回滚当前事务，已完成批次可安全重试。
7. 手动 prune 与按策略维护的存储原语可以共用；本轮不自行开启任何使用方的活跃历史淘汰政策。既有整 Thread TTL 删除继续复用可靠删除流程。

新日志和 GC 必须共同设计：不能一边删除回滚锚点，一边仍报告该 Run 可回滚；也不能永久 pin 所有历史却声称保留有界。显式裁剪后的可回滚范围必须在真实 HTTP/CLI 结果与文档中一致。

## 5. 性能目标与最低测量

### 结构性门槛

- 单次 Run 基线不复制未修改的历史 checkpoint/write 行；首次 claim 不全量 fetch 历史。
- 固定当前状态大小和本 Run 改动量时，日志字节随“受影响数据 + namespace 起始引用”增长，不随无关历史长度增长。
- prune 的客户端内存随批次和必要工作集受控，不存在 Python 中全 Thread 历史/全 blob 的 list/set/dict。
- GC 完成后没有可证明无引用的遗留 blob；所有存活状态、审批与仍有效回滚目标均可恢复。

### 固定对照输入

使用 100/1,000/10,000 个历史 checkpoint、1 KiB/16 KiB 负载、单/多 namespace、Delta 与普通 channel，对比固定单 Run 增量和多 Run 序列。Delta 的 10/50/200 是快照周期实验值，不是新增平台政策。

记录逻辑 payload、行数、WAL、表/索引大小、claim/写入/恢复 p50/p95、锁等待与独立 Worker 峰值 RSS。每组记录依赖、输入大小、并发数、命令、原始产物和失败；不能用 HTTP 超时值当作已完成延迟。容量较大时串行验证，避免测试本身挤占共享服务。

SQL DELETE 后逻辑数据可减少，但 PostgreSQL 物理文件未必立即缩小；分别报告逻辑字节、可复用空间和物理文件，不把 VACUUM FULL 或停库作为验收捷径。

绝对延迟、RSS和资源预算已在S02获得基线后由用户冻结，具体见文末。六组新实现满足门槛，原始数据见implementation；已有33.59%或2.06倍等使用方旧夹具数字不作为本次门槛。

## 6. 单格式切换、数据与恢复

不保留旧完整基线读路径、双写或多代 schema fallback；目标数据库与双包运行一套格式。无需为了旧 SDK 逐字段复制其历史错误行为，也不借机删除无关公开入口。

若保留现存数据，安排一次性离线转换，利用旧基线与当前行的实际差异建立新边界和必要撤销记录；验证逆序回滚、变更过的既有 write、已删除 namespace 和 projection。无法证明转换正确时停止并报告具体条目，不丢数据、不回退到在线旧逻辑。

维护窗口先排空/持久暂停所属执行，确认备份与恢复，再转换并让 API/Worker 同版本启动。混跑旧 Worker 明确拒绝。故障用演练过的数据库/配置快照恢复或修复后前进恢复，不承诺旧包可读新数据。

单格式要求不自动授权清空用户库或删除历史。破坏性清理仍须具体范围授权；隔离 fixture 的数据准备/回收与现役库严格分开。

## 7. 验证与正式包交付

最低验证包括：新增与改写 writes、主子图、Delta 快照前后、`update_state`、并行任务、HITL、中断恢复、fork、A→B→回滚 B→C、逆序批量回滚、后继依赖拒绝、晚写/旧 generation、进程崩溃、事务故障注入、维护竞争和跨授权范围不变。

先通过源码与候选安装的上述场景及容量门槛，再锁步提升双包唯一版本、构建 wheel/sdist 四产物，记录哈希与实际包含的修改，依次直接发布 runtime、CLI。PyPI 冷安装必须独立环境、正式源、核对版本/哈希/迁移 head/CLI，并重复关键真实数据库场景。

使用方只安装已验正式版本，不以本地 wheel 或相同版本号代替正式交付；GraphHarbor 自身测试不依赖使用方仓库。正式发布授权已经具备，生产服务切换没有因此获得授权。

阶段验证与整体验收见[verification.md](verification.md)。新日志、GC、迁移、容量和正式源独立安装的原隔离验收已通过；用户追加授权后完整正式源码已交付origin/main。远端补验发现live-server E2E的undo外键错误与JavaScript SDK线程更新422，当前项目部分完成，S07待修复复验。严格官方逐字段比较仍有既有差异，不宣称整套Server完全等价；未执行现役部署。

## 8. 实施位置与调用约束

下表路径均相对 GraphHarbor 仓库根。实现前检索全部调用者，修改共享存储路径一次；HTTP/CLI 不另写回收 SQL。

| 文件与入口 | 本轮计划 | 验证重点 |
|---|---|---|
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py`：`capture_baseline()`、`rollback_run()`、`complete_rollbacks()`、`prune_checkpoints()` | 替换全历史基线，按最小日志撤销，统一引用保护与 blob 回收 | 不复制未改历史；逆序回滚、故障重试和逐批恢复正确 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint.py`：`FencedPostgresSaver.aput()` / `aput_writes()` | 在既有写屏障事务中记录实际受影响键和必要 before-image | 校验、日志与 saver 写入同事务；新增/覆盖写均有证据 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py`：`RunRepository.claim_next()` | 首次 claim 只捕获起始引用和必要控制信息 | 历史增长不再增加完整历史 fetch；租约/锁顺序不变 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py` 与 `migrations/versions/` | 以最少新格式替换 `RunCheckpointBaselineRow` 的完整副本；一次性转换旧数据 | 转换与恢复有演练，不保留在线双格式；迁移编号核对当前 head 后确定 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` | 接入新回滚/投影恢复步骤，保留取消确认与持久恢复 | 崩溃、旧 generation、晚写和 HITL 恢复正确 |
| `libs/langhost/src/langhost/core_api.py`：`threads_prune()` 与 cancel 入口；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py`：`Threads.prune()` | 继续委托同一通用存储服务，返回真实结果 | 只处理授权集合，公开/内部入口不能绕过屏障 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/retention.py` | 核对已有整 Thread 删除与新日志引用/表的清理关系，按需接入 | 到期/手动删除不遗留新日志；不修改使用方保留政策 |
| `libs/langgraph-runtime-pg/tests/test_checkpoint_mutation_safety.py` 及既有 production/persistence/REST/SDK 测试 | 扩展真实 PG、并发故障和容量对照 | 复用现有隔离 fixture；通用图不依赖使用方源码或模型 |

单格式转换、正式发包和使用方现役切换是不同动作。S02 冻结后的新增持久化设计保留人工评审，已有修复方向和发包授权不重复申请；当前文档不能代替后续安全、容量与数据恢复证据。

## 9. 历史资料与项目关系

[9 月 checkpoint 安全治理](../20260924-checkpoint-mutation-safety/README.md)记录安全回滚、写屏障和保留祖先方案的来源；[Thread 生命周期](../20261030-thread-retention-lifecycle/README.md)记录通用到期与删除能力。本项目独立维护方案、任务和验收，不接管原任务；既有功能仍须回归，历史测试数不作为本项目通过证据。

## 2026-11-21 实施决策与验收预算

用户已批准开始实施全部开发项。平台在独立 Worktree 的 `feat/checkpoint-storage-governance` 分支开发，资源登记 `wt_3b7f1734875d`；完整验收后才迁回本地 main。GraphHarbor 仍独立交付通用底座。其后用户另行批准经验沉淀与两仓源码提交推送，未新增现役部署授权。

用户另行确认投影恢复使用标准 JSON Patch 反向差量，替代原优先从 checkpoint 重建的建议：构图可能执行使用方 I/O，不能放在恢复锁内；跨连接重建也无法读取尚未提交的撤销。反向差量与结果更新同事务保存，与行级撤销同事务恢复，不新增业务 reducer。大范围替换状态时，撤销数据仍可能与实际被替换内容等大，不宣称任意写入都固定大小。

用户已确认固定夹具的绝对门槛：同一隔离机、单 namespace、历史 100/1,000/10,000 条、负载 1 KiB/16 KiB，Run 边界不超过 8 KiB，建立边界 p95 不超过 100 ms，单次建立边界 WAL 不超过 64 KiB，维护进程峰值 RSS 不超过 256 MiB，10,000 条历史 prune 不超过 5 秒；同时检查这些新增维护开销不随旧历史负载线性增加。测量分别标明重复次数、版本、逻辑字节与物理文件；预算不外推整个 Agent 状态或永久历史绝对有界。
