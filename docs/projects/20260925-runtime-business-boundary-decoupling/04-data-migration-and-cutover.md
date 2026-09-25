# 04 历史数据迁移、联合切换与最终验收

## 目标

去掉核心固定业务隔离，同时保住已有资源可见性、幂等、防越权和恢复能力。迁移完成包括旧运行逻辑退出；仅新增 generic mode 并永久保留旧业务分支不算完成。

## 方案设计

### 1. 持久化事实和目标

| 当前依赖 | 当前位置 | 目标与约束 |
|---|---|---|
| Assistant/Thread/Run/Cron 的 tenant/project 列 | G libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py | 应用授权 metadata + 标准 Auth；最终删除运行时列依赖 |
| scope/status 索引 | 同上 ix_runs_scope_status | 根据实际通用查询重建索引，保留 worker claim/锁正确性 |
| scope/idempotency 唯一性 | uq_runs_scope_idempotency、run_store.py::RunRepository.create | API 对标准 identity 与匿名域生成不同的内部 key，再用单列唯一索引；不能直接把客户端原 key 设为全局唯一 |
| HTTP Store 前缀 | G libs/langhost/src/langhost/store_api.py | 核心停止解释 tenant/project；平台负责 namespace 策略 |
| 执行上下文 v1 | G auth.py/core_api.py/production_worker.py | 只保留通用 v2；旧任务离线处置，见 01 |
| 平台幂等提交 | P apps/platform-api/src/platform_api/modules/runtime_gateway/application/service.py::launch_runtime_run，及同模块 infra/sqlalchemy models/repository | 已有项目/thread/key 唯一审计与稳定 platform:SHA256(project,thread,key) 上游 key，直接复用 |

### 2. 历史运行数据清理与旧列退出

先做只读盘点：四类资源数量、NULL/__default、scope 与 metadata 不一致、assistant versions、内置 graph assistant、run/thread 不一致、cron、进行中任务和 Store namespace 分布。未知数据不得自动归入默认项目。

本次采用停机切换并清理历史数据，不引入新旧逻辑共存：

1. 在隔离环境完成新代码和迁移演练；新代码直接删除固定 Principal、业务 SQL、旧字段和旧快照运行分支，不加切换开关。
2. 切换时停止新请求写入、cron 和旧 worker，备份一致性数据。平台所有新建/更新仅由应用 Auth 写入受保护的 tenant_id/project_id metadata。
3. 在明确目标数据库和备份完成后删除旧 threads/runs/assistants/crons/checkpoints 运行历史；Store 数据由平台消费者盘点后另行决定，不能随运行历史隐式清理。不从旧列回填业务 metadata，也不把未知行归入默认项目。生产数据库未经单独指定不得执行清理。
4. 执行新 schema 迁移删除旧列和索引。新运行代码只依赖通用字段与应用 Auth。普通 metadata GIN 优先复用。
5. 历史 Alembic revision 仅保留升级历史；一次性转换脚本可以读取旧字段，但不得成为在线兼容层。两仓及所有 API/worker 同步升级，验收后开放写入。
6. 内置 graph assistant 的只读使用与管理修改分别授权；NULL 归属不等于所有人可写。无法判断归属的资源先隔离，不能自动分配默认项目。

此过程不重写 checkpoint IDs、父子链或 pending writes；沿用已修复的 checkpoint mutation 安全逻辑，迁移测试覆盖它。

### 3. 幂等键：已采用域化摘要

旧契约为 tenant/project/key 内重复；不同 target 冲突。平台已有自己的 submission 记录与上游稳定键。候选代码采用以下通用契约：

- 认证请求：以规范化标准 identity 和 key 确定幂等域，不认识 tenant/project；不同 target 在相同域重用 key 仍冲突。
- 无认证开发模式：显式 `anonymous:` 域；认证 key 使用 `auth:` 域，两者保存为带标签的 SHA-256 摘要，不让普通字符串 identity 与匿名标记碰撞。
- 同 key 重试先按当前权限检查既有 run/thread，禁止通过幂等命中返回已失权资源；同 target 不同请求体的目标行为在 P0 与参照比较后锁定，不顺带扩大功能。
- 现有 `runs.idempotency_key` 单列存内部域化摘要，并由 `uq_runs_idempotency` 保持事务冲突恢复；域仅来自标准身份或服务器匿名模式，不能开放任意客户端参数伪造。
- 这是现有扩展行为的变化，不能宣称同用户跨项目 raw key 仍自动隔离。平台现有上游 SHA256 已包含 project/thread，可保留平台业务语义；其余调用者必须有迁移说明。
- 用户已选择删除旧运行历史，不回填旧 key 或重启旧任务。迁移前仍须只读统计 NULL key、匿名边界和旧 scope 冲突；未知行不统一塞到 anonymous。
- 新 key 的并发请求、跨身份与跨 target 冲突均在隔离 PostgreSQL 演练，不能只靠 mock。

若锁定官方版本没有同等幂等扩展，单列 GraphHarbor 扩展 profile；官方协议比较与平台扩展测试分开，不能静默忽略差异。

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
| R2 | 按已批准范围清理旧运行历史，核对平台 ACL 与 Store；删除旧列/约束 | 清理对象与备份可核对，迁移无旧列和索引 |
| R3 | 两仓和所有进程一次升级，平台依赖新版本；新代码仅识别新契约 | 候选 wheel、ACL、run/SSE、workspace 烟测通过 |
| R4 | 开放写入，记录切换点与新增数据 | 无旧业务代码、旧运行格式或旧依赖残留 |

用户已明确：维护切换窗口允许直接删除历史 GraphHarbor 运行数据，不做旧业务身份回填。pending/running 任务在删除前停止并清点，未完成任务随历史数据一并丢弃；平台 ACL 与运行数据清理必须在同一维护窗口协调，不能让 ACL 悬挂记录被解释为可访问资源。该决定不授权对未知或生产数据库执行清理，目标环境、备份及维护窗口仍须在实际操作前明确。

故障恢复采用整套旧应用加切换前数据库备份恢复，不在新代码保留兼容分支。开放写入前失败可整体恢复；开放写入后先停止写入并导出/核对新增数据，再制定恢复或向前修复方案，不能直接用旧备份覆盖而丢失新增数据。

TTL、调度、后台清理等系统动作不冒充 end-user 请求，但必须按其既有对象集合工作；调度触发新业务执行的授权见 01。

## 任务拆分

| ID | 改动内容 / 代码位置 | 预期结果 | 验证项 | 状态 |
|---|---|---|---|---|
| D01 | 两仓版本、实际消费者、G models/run_store/Store/签名历史只读盘点；官方 probe | 数据/依赖/差异清单，确认幂等及 namespace 契约 | V-D01 | 部分完成：平台现役源码未发现 GraphHarbor Store 调用，官方 Auth 子集差分通过；本机 PG17 两库完成计数级盘点，Store 两表为空；外部消费者与生产目标仍未知 |
| D02 | G schema 迁移 + P 维护窗口 ACL 协调清理 | 旧运行数据清理后可迁移，备份可恢复 | V-D02 | 部分完成：隔离 PG17 已验证非空拒绝及旧 schema 备份恢复；本机两库已备份、协调清理 ACL/运行历史并升级，且两份归档已完整恢复到隔离库；生产回退组合未演练 |
| D03 | G run_store/models/migrations；P 既有稳定 key 接入测试 | 通用幂等域、无 NULL 穿透和跨域命中 | V-D03 | 部分完成：唯一索引改全局内部 key，API 用标准 Auth identity/匿名标签域化；PG17 并发提交和跨身份 2 passed。跨项目/响应丢失与平台联合验收未完成 |
| D04 | G store_api；P 实际 Store 消费者和 auth | namespace 授权及旧数据可读，无物理搬迁 | V-D04 | 部分完成：GraphHarbor 隔离 PG17 Store 跨身份搜索/列举、suffix/max_depth、TTL sweep 回归通过；本机 Store 两表为空，平台现役源码未发现消费者，外部消费者未知 |
| D05 | API/worker 格式版本切换、排队/恢复清点、候选包联调 | 维护窗口一次切换，无旧格式运行分支，故障恢复可操作 | V-D05 | 部分完成：公开 post33 安装及平台锁定、本机单项目业务 Run/SSE/HITL、PG17 worker 租约接管和两库归档隔离恢复已验证；长排队、worker 崩溃及过期身份组合仍缺 |
| D06 | 完整联合验收、发行说明、旧列/业务分支清理、能力文档更新 | 全部 Final 门禁通过后才能 done | V-D06—D08 | 部分完成：治理浏览器 10 passed，单项目 Run/HITL 与文件正向链路通过；官方 identity-only Auth 子集通过，但完整 OpenAPI 比较仍有 203 处差异，跨项目故障与回退门禁未完成 |

## 验证要求与记录

### Phase 验证

- [ ] V-D01：记录两仓实际 commit+diff、Python/SDK/API 版本、授权路径表、所有消费入口；只读报告包含数据数量/异常数量，不导出业务内容或凭据。
- [ ] V-D02：隔离 PostgreSQL 从旧 revision 升级；四表任一非空时拒绝，按批准范围清理后重试，核对旧列/索引已删除；备份恢复原 schema 与数据。生产维护步骤另需核对平台 ACL 与 Store，不做 metadata 回填。
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

**2026-09-25 PG17 与候选包阶段验证：** `graphharbor_boundary_pg17` 中独立 schema 从 `007_checkpoint_baselines` 种旧 thread；迁移 `008` 对 assistants/threads/runs/crons 任一非空明确拒绝，清理后可重复升级。用 PostgreSQL 17 的 `pg_dump -Fc -n gh_restore_probe_20260925_b1` 备份旧 schema，升级后丢弃仅该演练 schema，再以 `pg_restore --exit-on-error` 恢复；版本回到 `007_checkpoint_baselines`，一条 thread 的旧 tenant/project/metadata 值一致。临时 schema 已清理，归档仅在 `/tmp`。最终双 wheel/sdist 构建安装、无业务 workspace/备份文件；隔离 PG17 的 runtime-pg 全组 148 passed、18 skipped，langhost 独立进程 58 passed。生产 worker 租约丢失、重新入队和新 worker 完成已回归；平台真实 HTTP Thread ACL 链路通过。尚无生产目标清单、平台 ACL/Store 联合清理、完整 run/SSE、浏览器及官方全入口差分 Final。

**2026-09-25 Final 子集：** 隔离平台 API + 候选 Runtime + Web 的 `platform-access-governance.spec.ts` 全文件 10 passed；含服务账号项目 grant/token 即时撤销、管理员限时接管、子资源拒绝及浏览器身份失效。用户创建旧测试与现行一次提交表单不符，修正后全文件通过。此结果仅覆盖治理浏览器子集，不能勾选 V-D07；业务 run/HITL、文件正向操作、生产数据清单与切换回退仍缺。

**2026-09-25 追加诊断与准入：** 隔离 PG17 全组在 root run checkpoint fencing 修复后为 149 passed、18 skipped；官方 0.13.0 identity-only Auth/Thread/`runs/wait`/HITL/SSE 子集动态差分通过。平台现有 2142 服务的数据库缺少 Alembic `20260925_0005` 中 `thread_access.provisioning_status`，浏览器创建 Thread 和列表/计数均返回 500；平台 API 新增启动时列检查，临时 SQLite 旧 schema 启动拒绝测试通过。该服务使用 `--reload`，新代码热重启后按预期拒绝旧 schema 启动；其数据库未迁移，`scripts/local-stack.sh start` 会先运行迁移。平台源码静态盘点未发现 GraphHarbor HTTP Store 或注入式 Store 调用，不能推断目标库 Store 无数据。用户尚未指定维护目标、备份和窗口，未执行平台 ACL 协调清理或生产回退演练。

**本机 PG17 只读盘点（2026-09-25）：** 两份应用 `.env` 均指向本机 5432；Runtime 库 `graphharbor_acceptance` 在 `006_terminal_events`，Platform 库 `platform_api` 在 `20260922_0004`，未执行任何写入。Runtime 有 assistants 66、assistant_versions 66、threads 57、runs 324、crons 0、checkpoints 6,196、checkpoint_writes 9,526、checkpoint_blobs 1,912、runtime_events 434,742、store_items/旧 store 均 0。Run 状态为 success 218、interrupted 100、error 4、timeout 2；Thread 为 idle 51、interrupted 6。平台有 thread_access 57、run_requests 401；ACL 与 Runtime Thread ID 集合摘要一致，submission ledger 覆盖 73 个不同 Thread，不能假定它会随 Thread 清理自动消失。旧 scope 与 metadata 的不一致计数：assistants 55、threads 57、runs 324；这批旧记录不得直接进入无 scope 的新代码。应用表另有 runtime_message_inbox 5、Dear memory 3、Dear skills 2、skill bindings 0；记忆和 skills 属平台数据，不能用测试用的 `truncate_all()` 清理。Runtime 库约 7.7 GB，其中 runtime_events 约 7.7 GB；本机 `/tmp` 所在卷余量约 22 GB，实际备份位置及恢复空间须在窗口前确认。上述是本机配置指向的数据，不冒充生产盘点。

**本机切换前置步骤：** 确认目标确为上述两库和维护窗口；停止 API、worker、cron 与平台写入后再次核对 pending/running 数量；将两库分别做一致性备份并在隔离库恢复校验。清理仅限旧运行数据、其 checkpoint/event/lease/inbox 及平台对应 Thread ACL 与 run submission ledger，保留项目、用户、模型配置、Dear memory、skills 和 Store；清理范围按实际外键先核对，不能运行 `truncate_all()` 的 `CASCADE`。核对四类旧 scope 主表为空、平台 ACL/ledger 不再指向删除的 Thread 后，先升级 GraphHarbor 至 `008`、平台至 `20260925_0005`，再启动同版 API/worker 和平台，执行 Auth/业务 run/SSE/文件验收。任一步失败时保持停写，以整套旧应用和两库备份恢复；开放写入后不得直接覆盖新增数据。未确认目标和备份前，不执行这些步骤。

**本机执行记录（2026-09-25）：** 已确认本机 PG17 的 `graphharbor_acceptance`、`platform_api` 为本轮目标，并在停止 local stack 后核对无 pending/running Run。用 PostgreSQL 17 `pg_dump` 分别生成权限 0600 的 `/tmp/graphharbor-boundary-20260925-prewipe-runtime.dump`（约 2.8 GB）与 `/tmp/graphharbor-boundary-20260925-prewipe-platform.dump`（约 1.7 MB），`pg_restore --list` 成功；这只验证归档可列目录，**未完成两库全量恢复演练**。按明确表名 `TRUNCATE ... RESTRICT` 清理 Runtime 的旧 Assistant/Thread/Run/Cron、checkpoint、event、lease、retry、inbox，及平台 `thread_access`、`run_requests`，没有使用 `CASCADE`；Dear memory 3 条和 skills 2 个保留。平台迁移至 `20260925_0005`，Runtime 迁移至 `008_remove_business_scope`，四类资源表旧 scope 列均已删除。当前 Runtime 库约 11 MB，Thread/Run/Event 均为 0，平台 ACL/ledger 均为 0；本机栈的 8123 `/ready` 和 2142 `/_system/health` 返回健康。这不代表 V-D07/V-D08 通过。

**本机候选依赖限制：** 平台 `apps/runtime-service/uv.lock` 仍绑定同版本旧 PyPI wheel；从当前源码构建的双候选 wheel 已安装到该虚拟环境，但普通 `uv run --frozen` 会重新同步为旧 wheel。本轮栈用 `UV_NO_SYNC=1 bash scripts/local-stack.sh start` 启动，后续候选验收也必须保持 `UV_NO_SYNC=1`。正式交付前须锁定一个可重复安装的新版本或可审查的本地候选来源，否则用户直接启动将测到旧代码。

**2026-09-25 依赖来源更新：** GraphHarbor 双包 `0.13.0.post33` 已从本次源码构建并上传 PyPI；公开索引的隔离安装确认 `graphharbor` 与 `graphharbor-runtime` 均为 post33。平台 runtime-service 已将 `pyproject.toml` 与 `uv.lock` 同步到 post33，普通 `uv sync --frozen` 将原本机临时候选 post32 替换为公开 post33；`uv run --frozen` 双包导入和版本检查通过。上段限制是历史记录，不再是当前启动条件。业务 Run/SSE/HITL、文件与 Final 仍须另行验收。

**2026-09-25 正式依赖本机联调：** 平台 `local-stack.sh restart` 不设 `UV_NO_SYNC`，Runtime 迁移到 `009_event_retention_watermarks`，API/worker/platform-api/Web 全部就绪。runtime-service 的 Auth/模型/工具/资源绑定/workspace 定向测试 99 passed；通过平台登录和 `x-project-id` 的网关创建 Thread 200、创建 `workflow_demo` Run 200，Run success、state 200、历史 SSE 有 16 帧。随后仅向本机 worker 进程注入 `~/.my_best/.env` 的 `miaomiaoai` 代理凭据及 `deepseek-v4.1-flash`，真实模型 Run success 且响应非空；另一次含“需要人工确认”的 Run 为 interrupted、1 个 interrupt，按平台恢复契约只提交 `command.resume` 后新 Run success、响应非空、interrupts 清零。首次恢复请求额外携带 `assistant_id` 被平台按 `resume_configuration_override` 以 400 正确拒绝，该次不计成功。此证据覆盖本机单项目管理员链路，不替代跨用户 ACL、文件、恢复或 Final。

**2026-09-25 完整备份恢复：** 用本机 PostgreSQL 17 的 `pg_restore --exit-on-error --no-owner --no-acl` 将两份切换前归档分别恢复到新库 `graphharbor_boundary_restore_post33` 和 `platform_boundary_restore_post33`；两个命令均退出 0。Runtime 归档恢复后 revision 为 `006_terminal_events`，有 Threads 57、Runs 324、Events 434,742、Store items 0，库约 7.8 GB；平台归档恢复后 revision 为 `20260922_0004`，有 Thread ACL 57、run requests 401，库约 29 MB。这证明本机备份可完整恢复到隔离库，不等于生产环境切换后的全量回退演练。首次误用 PATH 上 PostgreSQL 14 的 `pg_restore`，因归档头 1.16 失败；两个新库仍为空，改用 PG17 后成功。恢复库仅用于只读核对，不覆盖现有业务库。

**2026-09-26 官方契约续验：** 使用同一 identity-only Auth fixture 启动锁定的 `langgraph-api==0.13.0` 开发服务与 GraphHarbor post33（独立 PG17 `graphharbor_boundary_auth_verify`、Redis DB15、测试签名密钥及独立 API/worker）。`check_boundary_auth.py` 的 12 类结果一致，包括创建 metadata 受信修改、跨用户 Thread/Run/state/history/search 拒绝、`runs/wait`、HITL 与 SSE 拒绝。随后执行 `scripts/compare_official_protocol.py --official-url http://127.0.0.1:31398 --graphharbor-url http://127.0.0.1:31399 --result-out /tmp/graphharbor-boundary-protocol-20260926.json`：比较失败，203 处差异全部位于 OpenAPI，其中路径/操作请求响应及参数 140 处、`components.schemas` 63 处。比较器现会识别 requestBody/schema 变化；此结果属于真实协议缺口，不得标 V-D06 通过。首次未设置签名密钥的 500 和未启动 worker 的超时均是隔离启动配置失败，不计成功；临时服务已停止。当前结果文件仅在本机 `/tmp`，后续需对差异按官方目标与明确排除项逐条分类并补行为验证。

**2026-09-26 平台创建故障注入：** `create_thread` 的 ready 确认失败、5xx 后对账探测或委托签发失败、明确 4xx 后 ACL 清理失败，以及 ACL 预留记录缺失，均有定向测试；未知结果保留可用于受限 reconcile 的 UUID，缺 ACL 不报告 ready。平台五个 ACL/gateway/委托/SDK 测试模块共 72 tests、3 skipped；提交钩子 Ruff 和格式检查通过。明确 4xx 后清理数据库失败仍可能遗留 pending ACL，运行端没有 Thread；需平台侧人工核对清理，此场景不算自动补偿成功。GraphHarbor 不承担该业务 ACL 清理。

## 状态

partial：D02/D03/D04/D05 有阶段实现；本机两库盘点、ACL 协调清理、schema 切换、归档隔离恢复、post33 可重复安装及单项目业务 Run/HITL/文件正向链路已有证据。官方完整 OpenAPI 对比仍有 203 处差异；跨项目与多用户拒绝、故障注入及 Final V-D06—D08 未完成。worker 租约恢复与 identity-only Auth 子集不能替代平台业务联合验收；不得据此切换生产。
