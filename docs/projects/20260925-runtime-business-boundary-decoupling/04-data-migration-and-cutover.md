# 04 历史数据迁移、联合切换与最终验收

## 目标

去掉核心固定业务隔离，同时保住已有资源可见性、幂等、防越权和恢复能力。迁移完成包括旧运行逻辑退出；仅新增 generic mode 并永久保留旧业务分支不算完成。

## 方案设计

### 1. 持久化事实和目标

| 当前依赖 | 当前位置 | 目标与约束 |
|---|---|---|
| Assistant/Thread/Run/Cron 的 tenant/project 列 | G libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py | 应用授权 metadata + 标准 Auth；最终删除运行时列依赖 |
| scope/status 索引 | 同上 ix_runs_scope_status | 根据实际通用查询重建索引，保留 worker claim/锁正确性 |
| scope/idempotency 唯一性 | uq_runs_scope_idempotency、run_store.py::RunRepository.create | 通用身份与幂等契约；禁止直接换成全局唯一 key |
| HTTP Store 前缀 | G libs/langhost/src/langhost/store_api.py | 核心停止解释 tenant/project；平台负责 namespace 策略 |
| 执行上下文 v1 | G auth.py/core_api.py/production_worker.py | 只保留通用 v2；旧任务离线处置，见 01 |
| 平台幂等提交 | P apps/platform-api/src/platform_api/modules/runtime_gateway/application/service.py::launch_runtime_run，及同模块 infra/sqlalchemy models/repository | 已有项目/thread/key 唯一审计与稳定 platform:SHA256(project,thread,key) 上游 key，直接复用 |

### 2. metadata 回填与旧列退出

先做只读盘点：四类资源数量、NULL/__default、scope 与 metadata 不一致、assistant versions、内置 graph assistant、run/thread 不一致、cron、进行中任务和 Store namespace 分布。未知数据不得自动归入默认项目。

本次采用停机切换并清理历史数据，不引入新旧逻辑共存：

1. 在隔离环境完成新代码和迁移演练；新代码直接删除固定 Principal、业务 SQL、旧字段和旧快照运行分支，不加切换开关。
2. 切换时停止新请求写入、cron 和旧 worker，备份一致性数据。平台所有新建/更新仅由应用 Auth 写入受保护的 tenant_id/project_id metadata。
3. 在明确目标数据库和备份完成后删除旧 threads/runs/assistants/crons/checkpoints/store 数据；不从旧列回填业务 metadata，也不把未知行归入默认项目。生产数据库未经单独指定不得执行清理。
4. 执行新 schema 迁移删除旧列和索引。新运行代码只依赖通用字段与应用 Auth。普通 metadata GIN 优先复用。
5. 历史 Alembic revision 仅保留升级历史；一次性转换脚本可以读取旧字段，但不得成为在线兼容层。两仓及所有 API/worker 同步升级，验收后开放写入。
6. 内置 graph assistant 的只读使用与管理修改分别授权；NULL 归属不等于所有人可写。无法判断归属的资源先隔离，不能自动分配默认项目。

此过程不重写 checkpoint IDs、父子链或 pending writes；沿用已修复的 checkpoint mutation 安全逻辑，迁移测试覆盖它。

### 3. 幂等键：先明确行为，再改索引

现状为 tenant/project/key 内重复；不同 target 冲突。平台已有自己的 submission 记录与上游稳定键。推荐 GraphHarbor 新通用契约为：

- 认证请求：以规范化标准 identity 和 key 确定幂等域，不认识 tenant/project；不同 target 在相同域重用 key 仍冲突。
- 无认证开发模式：显式独立匿名域；有/无身份的编码必须有类型标签，不让普通字符串 identity 与匿名标记碰撞。
- 同 key 重试先按当前权限检查既有 run/thread，禁止通过幂等命中返回已失权资源；同 target 不同请求体的目标行为在 P0 与参照比较后锁定，不顺带扩大功能。
- 可用规范 JSON 元组的摘要生成内部非空幂等域；唯一约束使用 (domain, key)，保持事务冲突恢复。域仅来自标准身份，不能开放任意客户端参数伪造。
- 这是现有扩展行为的变化，不能宣称同用户跨项目 raw key 仍自动隔离。平台现有上游 SHA256 已包含 project/thread，可保留平台业务语义；其余调用者必须有迁移说明。
- 旧 key 回填先从可信执行身份或平台 submission 映射重建。v1 过期不等于历史签名不可校验，但迁移验证与 worker 执行授权必须分离，不能以迁移为借口重新执行过期任务。
- 身份无法恢复、跨旧 scope 合并后出现碰撞、NULL key/匿名边界必须在只读报告列出并阻止切换。未知行不统一塞到 anonymous。
- 保存旧→新 domain/key 映射和约束备份；回填、并发请求、worker claim 均在隔离 PostgreSQL 演练，不能只靠 mock。

若 P0 发现锁定官方版本没有同等幂等扩展，单列 GraphHarbor 扩展 profile；官方协议比较与平台扩展测试分开，不能静默忽略差异。最终 domain/key 方案需在 D01 评审确认后执行 D03。

### 4. Store namespace

当前 HTTP 自动加 (\"__graphharbor__\", tenant, project)，响应自动去前三段。删除这两个函数会改变物理访问和返回值。

推荐最少数据改动路线：

- **已有物理 namespace 先不搬。** 平台作为 namespace 的业务所有者继续使用原字节前缀，GraphHarbor 只把它当普通 namespace 数组。
- 通用 Store handlers 依照锁定官方 Auth Store 事件验证/修改 namespace、prefix/suffix；核心不再硬编码三段前缀或自动隐藏固定段。
- 平台若有 HTTP Store 消费者，由平台调用适配负责把业务相对 namespace 转为完整 namespace，并在平台返回边界恢复其业务显示；不能在 GraphHarbor 保留 tenant/project 响应特判。
- 平台当前网关未发现公开 Store 转发入口；正式切换前需扫描 graph 内部 Store 和其他脚本消费者、数据库实际数据。不能据此认定“无 Store 数据”。
- 若消费者使用 Auth 修改 namespace，则官方对“修改后 namespace 的响应形式”先做差分；不承诺回调自动提供反向重写。旧相对 HTTP 客户端的迁移属于显式契约变更。
- list_namespaces 空 prefix、suffix、max_depth、分页必须限制在授权根内；绝不允许为了兼容列出所有项目 namespace。
- 图内直接使用 Store 的应用自行遵守平台 namespace 约定；HTTP Auth 不自动保护 graph Python 内部调用。现有个人记忆 namespace/ACL 不强行统一到旧 HTTP 前缀。
- 暂不建通用 namespace mapper、双读所有前缀或全库重写工具；确有碰撞/新布局需求才另行评审物理迁移。

### 5. 一次性联合切换与故障恢复

用户已明确不要求旧业务兼容。因此取消双写、双读、reader-first/writer-last、旧字段别名及旧客户端适配；这是对上一版分批兼容升级方案的替换。

| 顺序 | 内容 | 退出条件 |
|---|---|---|
| R0 | 锁定两仓实际 commit+diff、依赖、数据清单；完整候选版本在隔离环境验证 | 新授权、平台业务链路、官方协议测试通过 |
| R1 | 维护窗口停写、停 cron、停止旧 API/worker；运行任务排空或逐项处置；一致性备份 | 没有旧进程继续写入；备份恢复已演练 |
| R2 | 离线迁移 metadata、幂等域、保留任务的身份快照；删除旧列/约束 | 数据归属、数量、映射、冲突检查通过 |
| R3 | 两仓和所有进程一次升级，平台依赖新版本；新代码仅识别新契约 | 候选 wheel、ACL、run/SSE、workspace 烟测通过 |
| R4 | 开放写入，记录切换点与新增数据 | 无旧业务代码、旧运行格式或旧依赖残留 |

用户已明确：维护切换窗口允许直接删除历史 GraphHarbor 运行数据，不做旧业务身份回填。pending/running 任务在删除前停止并清点，未完成任务随历史数据一并丢弃；平台 ACL 与运行数据清理必须在同一维护窗口协调，不能让 ACL 悬挂记录被解释为可访问资源。该决定不授权对未知或生产数据库执行清理，目标环境、备份及维护窗口仍须在实际操作前明确。

故障恢复采用整套旧应用加切换前数据库备份恢复，不在新代码保留兼容分支。开放写入前失败可整体恢复；开放写入后先停止写入并导出/核对新增数据，再制定恢复或向前修复方案，不能直接用旧备份覆盖而丢失新增数据。

TTL、调度、后台清理等系统动作不冒充 end-user 请求，但必须按其既有对象集合工作；调度触发新业务执行的授权见 01。

## 任务拆分

| ID | 改动内容 / 代码位置 | 预期结果 | 验证项 | 状态 |
|---|---|---|---|---|
| D01 | 两仓版本、实际消费者、G models/run_store/Store/签名历史只读盘点；官方 probe | 数据/依赖/差异清单，确认幂等及 namespace 契约 | V-D01 | 待开始 |
| D02 | P 迁移脚本 + G 可扩展 schema/metadata 查询 | 离线可恢复转换、冲突拒绝、数据一致 | V-D02 | 部分完成：G `008_remove_business_scope` 删除四表旧列及索引；PG17 空隔离库已升级。旧 head 非空迁移、冲突/恢复及平台清理演练未完成 |
| D03 | G run_store/models/migrations；P 既有稳定 key 接入测试 | 通用幂等域、无 NULL 穿透和跨域命中 | V-D03 | 部分完成：唯一索引改全局 key，API 用标准 Auth identity 域化；PG17 并发提交 1 passed。跨 identity/项目/响应丢失与平台联合验收未完成 |
| D04 | G store_api；P 实际 Store 消费者和 auth | namespace 授权及旧数据可读，无物理搬迁 | V-D04 | 待开始 |
| D05 | API/worker 格式版本切换、排队/恢复清点、候选包联调 | 维护窗口一次切换，无旧格式运行分支，故障恢复可操作 | V-D05 | 待开始 |
| D06 | 完整联合验收、发行说明、旧列/业务分支清理、能力文档更新 | 全部 Final 门禁通过后才能 done | V-D06—D08 | 待开始 |

## 验证要求与记录

### Phase 验证

- [ ] V-D01：记录两仓实际 commit+diff、Python/SDK/API 版本、授权路径表、所有消费入口；只读报告包含数据数量/异常数量，不导出业务内容或凭据。
- [ ] V-D02：隔离 PostgreSQL 从旧 revision 升级；覆盖 metadata 缺失/冲突、NULL scope、内置 assistant、versions、cron 和孤立 run；中断重跑幂等，迁移前后对象数及抽样摘要一致。
- [ ] V-D03：同 key 并发、不同 identity、相同用户跨项目、不同 target、匿名、旧 key 碰撞、权限撤回后重试、API 响应丢失；不多建 run、不返回他人 run。保留已有并发提交/锁测试。
- [ ] V-D04：原 namespace 数据 get/search/delete/list 与 TTL/索引均保持；跨项目、空前缀、suffix/max_depth、graph 内直接 Store 均验证。
- [ ] V-D05：新格式排队、重试/after_seconds、HITL、重启；旧任务离线转换或重新授权；旧快照在新运行端明确拒绝；整套备份恢复通过，禁止伪造身份补签。

### Final 验证（不得用 Phase 记录代替）

- [ ] V-D06 协议与通用应用：官方 0.13.0 + SDK 0.4.3 + 同一 Auth fixture，对比请求/响应/OpenAPI/SSE、授权调用和拒绝副作用。identity-only 与非平台字段应用必须通过。
- [ ] V-D07 平台关键链路：两个项目、多个用户，私有/共享/接管/撤权；创建→run/SSE→中断审批→恢复→历史/分叉→取消/删除；模型/BYOK/工具禁用；workspace/成果/terminal/skills/个人记忆。未启用的产品入口必须验证显式拒绝。
- [ ] V-D08 候选 wheel、安全和回退：干净安装无 langgraph-api 生产依赖；无核心 workspace/业务字段特判；签名快照/密钥不出现在公共输出；迁移备份恢复与所有回退组合演练。

建议性能门槛：同机、同数据、确定性 graph，在变更前后比较授权后列表、run 接收和 SSE 首包，排除模型耗时。默认 p95 不增加超过 20% 或 50ms（二者取较宽者）；无越权、重复 run 或新增 5xx；记录回调次数与超时率。至少覆盖 100/1,000/10,000 条平台会话及并发读写。此为提议验收门槛，评审时结合既有服务 SLO 确认，不是测量结果。

现有协议比较器和非法请求修复继续复用；给差分测试增加授权维度和故意破坏 filter/schema 的负控，证明测试能发现回归。“schema 零差异”不能替代授权、数据和行为验证。已有公开兼容差异逐项保留，不为本专项清零而忽略。

### 本轮记录

2026-09-25：仅源码、参照实现和方案文档核对。**功能、真实数据库、官方动态差分、性能、浏览器、真实模型、迁移和回退均未执行。**

文档检查已执行：6 份新增文档、12 个相对链接、9 个完整代码路径引用均通过自动存在性校验；4 个专题的规定章节齐全。两仓 git diff --check 通过；平台既有 scripts/check_docs.py 的 self_check 和本轮 3 个平台文档检查通过。此结果仅证明文档结构与引用，不证明计划中的功能已实现。

## 状态

partial：D02/D03 有核心阶段实现；D01 数据盘点、旧 head 非空迁移、Store、worker 恢复与 Final V-D06—D08 未完成。不得据此切换生产。
