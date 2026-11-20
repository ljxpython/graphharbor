# Checkpoint 存储增长治理 - 任务与进度

**项目状态：已完成（done，批准的通用存储范围）。** 启动日期：2026-11-20。S01–S07完成；正式双包0.13.0.post49已发布并独立冷安装验证。原安全治理任务与测试保留在原项目，不据此关闭其完整官方等价性等未完成项。


详细目标、设计、边界和性能测量见 [plan.md](plan.md)。使用方接入进度留在[平台同名关联项目](../../../../ai-agent-platform/docs/projects/20261120-checkpoint-storage-governance/tasks.md)，不成为 GraphHarbor 的安装、测试或发包依赖。

## 任务清单

### S01：确认范围、通用边界和规划交付

使用方需要减少长 Thread 重复存储，同时保持既有恢复和回滚能力。本任务明确通用引擎与业务状态/期限的边界，避免修复时把使用方逻辑写入 GraphHarbor。

**状态：** 已完成（done，仅独立项目规划交付）。
**完成标准：** 当前机制、目标设计、任务、验收、发包授权与单一实现要求落文档；摘要/导航一致，文档检查通过。
**涉及位置：** 本项目 README/plan/tasks/verification/storage-plan，以及本仓 CONTEXT/FEATURES/README。

- [x] 记录完整历史基线、无 blob GC 和历史加载路径，区分源码证据与真实容量。
- [x] 明确不引入使用方 Agent、模型、工具、目录或保留天数；独立验收只用通用确定性图。
- [x] 记录双包直接发布授权、秘密临时注入与打包范围审查；不扩为 Git 或现役部署授权。

**开发与验证进度：** 方案、S01–S07和验收已迁入独立项目，原项目本轮增补已移出。两仓原9份历史文档逐字节还原，规划阶段文档与导航检查通过。“当时未改源码/读取密钥”是启动事实；后续实施、预算批准和正式发布分别记在S02–S07。
**下一步：** 无剩余规划项；保持通用边界与授权范围。

### S02：冻结依赖、写入语义和容量基线

实施者需要知道 saver 究竟新增或更新哪些键，以及历史长度在哪些环节放大开销，才能选最少日志与安全 GC，而不是凭表名猜测。

**状态：** 已完成（done）。
**完成标准：** 精确目标依赖、saver 写入键/upsert、Delta snapshot/counter/祖先语义与所有调用者有证据；固定夹具基线完成，绝对性能预算在新实现验收前冻结。
**涉及位置：** 双包 `pyproject.toml`、`uv.lock`、`checkpoint.py`、`checkpoint_mutations.py`、`run_store.py`、当前迁移和真实 PG 测试。

- [x] 固定 LangGraph 1.2.24、checkpoint 4.3.2、checkpoint-postgres 3.2.1、SDK 0.4.14；真实 PG 用例验证 Delta/update_state/fork 后继续恢复。
- [x] 核对三个原生表主键与 upsert：checkpoint/writes 可能覆盖，blob 的同键 DO NOTHING 不生成误撤销日志。
- [x] 完成六组固定夹具；旧版最后一组因 JSONB 数组超过 256 MiB 失败，新版六组全部通过用户批准预算。RSS 为独立维护进程，锁竞争另由确定性安全用例验证。
- [x] 用户批准同事务标准 JSON Patch 反向投影、首次 before-image 日志和绝对预算，采用单一 journal-v1 格式及 migration015。

**开发与验证进度：** 2026-11-21用户批准全部开发、标准JSON Patch反向投影与绝对预算。旧实现10,000×16KiB writes因单个JSONB超过256MiB建立边界失败，其余五组及新实现六组均留存原始数据。精确依赖1.2.24/4.3.2/3.2.1、SDK0.4.14已通过真实PG与正式冷安装验证，随post49发布；平台环境准备只在关联P02维护。

此前依据（历史）： 现有 1.2.11/4.2.0/3.1.2 源码和无数据库检查已确认风险机制；没有本阶段真实容量数据或目标版本结论。
**下一步：** 依赖组合和预算已冻结；结果用于 S06/S07，不将维护预算外推为整个 Agent 状态永久有界。

### S03：用最小边界与撤销日志替代完整基线

新 Run 需要可撤销自己的持久化影响，但不应先复制 Thread 从创建以来的全部历史。本任务只保存起始引用、必要控制状态和受影响行的首次变更前值。

**状态：** 已完成（done）。
**完成标准：** claim 不读取/复制无关历史；日志与 saver 写入同事务；新增/改写的 checkpoint/writes 可恢复，投影不再按 Run 复制完整历史，逆序回滚和依赖拒绝正确。
**涉及位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` 下 `checkpoint_mutations.py`、`checkpoint.py`、`run_store.py`、`models.py`、必要迁移；调用处与真实 PG 测试。

- [x] claim 只查根 head，触碰子 namespace 时懒记录 head；按真实主键保存首个 before-image，包含 root Run。
- [x] PG AFTER 触发器与 saver 同事务记日志，保留 run → thread 锁与 owner/generation/lease 校验。
- [x] 按用户批准调整为同事务 JSON Patch 反向差量，不加载业务 graph/reducer，也不逐 Run 复制完整 values。
- [x] 回滚按 sequence 拒绝后继依赖，原子恢复行与投影；覆盖逆序、失败重试、pending writes 和晚写拒绝。

**开发与验证进度：** 新边界、日志、投影与调用方已实现。真实 PG 专项验证同键两次覆盖只存首次 before-image；同 version blob 未被误删，大投影追加只记录少量反向操作。必要控制字段、实际大范围替换的撤销量仍可能很大，不承诺任意写入固定大小。
**下一步：** 无剩余开发项；正式候选和冷安装已验证，生产切换另行授权。

### S04：实现引用安全、有界 prune 与 blob GC

使用方显式裁剪恢复点后，不再被存活状态引用的 blob 应可回收；当前状态、Delta 祖先、pending writes 和仍有效回滚锚点必须可恢复。

**状态：** 已完成（done）。
**完成标准：** 所有维护入口共用通用服务；受保护一致视图内确定引用，分页/批量完成回收；无全历史 Python 集合；每批后均可恢复，重复与失败重试安全。
**涉及位置：** `checkpoint_mutations.py`、`checkpoint.py`、`ops.py`、`libs/langhost/src/langhost/core_api.py` 的维护调用及测试。

- [x] SQL 递归计算各 namespace head 与逐 channel Delta 祖先；从历史 metadata 发现 channel，覆盖 update_state 头缺 counter 情况。
- [x] 显式 prune 先关闭旧回滚窗口，再按存活 channel/version 引用回收 blob；活跃 Run/rollback/删除屏障保持。
- [x] 临时表与每批 500 行 DELETE，在同一事务持 Thread 锁至提交，不向 Python 拉取全历史。
- [x] 缺祖先明确拒绝；重复及注入故障后状态可恢复。不物化业务 channel，不开启活跃历史淘汰。

**开发与验证进度：** 真实 PG 已验证无引用 blob 实际减少，Delta/update_state/fork 后 prune 可重连并继续执行；注入删除失败回滚整个维护事务，读者看不到批次中间状态。10,000 条夹具维护小于 0.71 秒。
**下一步：** 无剩余开发项；正式包同矩阵验证已完成，不自行开启活跃历史淘汰。

### S05：实现一次性数据转换与故障恢复

维护者需要切换到单一新格式，同时保护需保留的数据。转换是一次性维护动作，不把旧基线读写长期留在运行时。

**状态：** 已完成（done，隔离演练）。
**完成标准：** 隔离副本完成转换、一致性核对、中途失败续跑/前进恢复及备份恢复；API/Worker 仅运行匹配新格式，旧 worker 明确拒绝。
**涉及位置：** 当前迁移 head 之后的必要迁移、最小转换入口、`schema_setup.py` 和恢复测试/操作文档。

- [x] migration015 拒绝活租约，要求先停止 API/Worker 并备份；转换保留数据，不清业务库。
- [x] 旧基线与下一边界/当前行在 PG 内求差，覆盖已有键覆盖、已删 namespace、投影及逆序回滚；所有权无法证明时事务中止并标明 Run。
- [x] 没有在线旧格式分支；已演练转换故障退回014、修复后前进，以及 pg_dump/pg_restore 后重新转换。Schema 不匹配拒绝启动，旧 writer 缺 journal-v1 被原子拒绝。

**开发与验证进度：** 两个真实 PG 迁移参数场景通过，恢复前后 checkpoints/writes/投影逐项一致；旧 blob 归属无法证明时保留，后续引用 GC 回收。迁移前备份是回退依据，downgrade 明确拒绝。未迁移现役库。
**下一步：** 使用方按同一操作顺序演练；现役切换须另有具体授权。

### S06：验证真实 PG、安全、并发与容量

使用方需要独立可安装的通用引擎，既减少重复存储，也在并发取消、审批与进程故障后保存正确状态。

**状态：** 已完成（done）。
**完成标准：** verification 新矩阵及 S02 冻结的绝对预算通过，原始证据可复跑；无业务仓库依赖，数据安全/授权拒绝和实际支持范围明确。
**涉及位置：** 现有 checkpoint/production/persistence/REST/SDK 测试与最小容量夹具；本项目 verification。

- [x] 覆盖新增/覆盖 writes、并行批次、Delta 快照前后、update_state/fork、HITL、重启、逆序回滚、后继依赖拒绝与晚写/旧 generation。
- [x] 注入写入/撤销/GC/转换失败，验证原子性、可重试恢复、维护竞争及授权范围外不变。
- [x] 固定输入对照新旧实现的逻辑字节、WAL、p50/p95、锁等待和独立维护进程 RSS；物理文件不立即缩小不等同逻辑回收失败。
- [x] 审查源码/迁移/SQL/配置/指标无新增业务概念，保留不相关既有工作树修改，逐项声明 skip 和未覆盖条件。

**开发与验证进度：** 源码扩大回归399 passed/22 skipped、独立Cron3 passed，补启动校验后关键矩阵24 passed；最终stream/SDK11项通过。独立wheel关键37项、sdist26项及最终stream/SDK11项通过；正式源42 passed/1 deselected。套件重叠不加总，22项skip不算通过。六组容量全部通过已批预算，锁竞争另有确定性屏障测试。官方严格对照已实际执行，仍有既有201项OpenAPI及扩展/stream差异，不宣称严格等价。
**下一步：** 本任务无剩余开发；完整官方逐字段等价仍属原专项范围，生产容量另验。

### S07：锁步正式发布与独立安装交接

使用方需要从正式源安装经过验证的同版本 CLI/runtime，不能只依赖工作区源码或本地 wheel。

**状态：** 已完成（done）；按本专项直接正式发包授权执行。
**完成标准：** 唯一新版本的双包 wheel/sdist 四产物、哈希、迁移 head、实际打包范围和正式源冷安装有证据；独立环境关键 PG 场景通过，使用方可准确锁定。
**涉及位置：** 双包版本声明/锁文件、[发布流程](../../standards/release-process.md)、正式产物及本项目验证记录。

- [x] 构建前核实当前未提交/未跟踪修改的实际打包范围；旧源码60文件与正式post48一致，新轮子与当前源码一致，无私有配置、平台代码或Workspace。
- [x] 候选门禁后临时注入用户指定位置的令牌，先 runtime 后 CLI 正式发布，不输出/复制/持久化秘密。
- [x] 独立无缓存冷安装正式PyPI源，核对四产物哈希、版本/导入位置/CLI/迁移head015；42项关键真实PG/SDK/CLI测试通过。
- [x] 更新本项目任务、README、CONTEXT/FEATURES/适用标准与发布说明，交接使用方；发包不等于现役部署或Git授权。

**开发与验证进度：** 2026-11-21双包0.13.0.post49正式发布，四产物SHA256与PyPI逐项相同；Python3.13新环境从正式源无缓存安装，实际导入site-packages、CLI和head015符合。首次安装遇到索引同步延迟，未重复上传；补足测试专用asgi-lifespan后，最终42 passed/1 deselected。未纳入的内部上游ops入口需可选langgraph-api，公共生产路径不依赖该包。详见[发布证据](implementation/release-post49.json)及verification的Final。
正式源另外完成HTTP/Python/JS取消24场景，结果为`{"graphharbor":24}`。

**下一步：** GraphHarbor批准范围已交付；平台正式接入由其P03/P07验收，现役切换及本仓Git操作未执行。
