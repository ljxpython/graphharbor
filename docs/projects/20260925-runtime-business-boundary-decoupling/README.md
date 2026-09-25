# GraphHarbor 与 AI Agent Platform 业务边界解耦

## 项目概述

- **启动日期：** 2026-09-25（Asia/Shanghai）
- **目标：** GraphHarbor 提供通用 LangGraph Agent Server；平台负责业务身份、ACL、模型与工具策略、业务观测和 workspace。
- **改动级别：** 治理改动，跨仓库，涉及鉴权、持久化隔离、后台执行与升级回退。
- **状态：** `partial`。Worker 业务字段、workspace 源码及核心 SQL tenant/project scope 已完成代码移除；Thread 创建未知结果可用 UUID 对账。本机 PG17 两库已备份、清理旧运行数据并升级到新 schema，候选包 local stack 已启动。完整备份恢复、业务 run/HITL、浏览器文件正向链路及最终联合验收仍缺证据。
- **当前安排：** 2026-09-25 起暂停本专项的后续实施，先规划并处理[Runtime 流事件保留治理](../20260925-runtime-event-retention/README.md)；`partial` 是实施状态，不因暂停改为完成。
- **负责人：** 待指定；评审人由用户指定，AI 不代替人工批准。
- **预计工作量：** 12—18 人天，含联合验证；历史数据量、第三方消费者和官方授权差分结果可能调整估算。未承诺完成日期。
- **事实基线：** 本地源码，GraphHarbor / platform runtime 声明版本均为 0.13.0.post32；兼容参照为 langgraph-api 0.13.0、langgraph-sdk 0.4.3。不把在线文档更新自动当成升级目标。

**2026-09-25 用户决策：彻底解耦，不维护旧业务接口、旧字段语义或旧版本混跑兼容。两仓完成适配后维护窗口一次切换；官方 LangGraph API/SDK 契约仍是产品目标。历史数据允许在明确的维护切换窗口直接删除，不做旧业务字段回填。**

2026-09-25 实施状态：已在本机配置指向的 `graphharbor_acceptance` 和 `platform_api` 执行停写、备份、旧运行数据清理及 schema 升级；这不是生产切换。候选代码已移除 tenant/project SQL 隔离；隔离 PG17 迁移拒绝与备份恢复、跨用户 Thread 拒绝路径已验证。完整入口授权矩阵、业务验收及本机两库备份的完整恢复演练仍须完成。

本目录为跨项目方案唯一事实源。平台仓库同名项目只维护导航与责任入口，任务和验证结果只更新本目录对应专题。

## 阅读顺序

1. [身份、标准授权与平台 ACL](01-identity-and-authorization.md)：先补齐通用授权执行链，再去掉固定身份结构。
2. [模型与业务 trace](02-model-and-tracing.md)：复用 graph factory 和平台观测层，消除 worker 的字段特判。
3. [Workspace 与包边界](03-workspace-and-packaging.md)：平台已有实现，重点是调用核对、安全覆盖和发行包清理。
4. [历史数据迁移、联合切换与验收](04-data-migration-and-cutover.md)：隔离列、Store、幂等键、执行快照及最终准入。
5. 平台协作入口：同级检出的 ai-agent-platform 仓库内 `docs/projects/20260925-runtime-business-boundary-decoupling/README.md`。

## 调研结论

| 需求 | 源码确认的现状 | 本次处理 |
|---|---|---|
| model / platform trace | worker 主动提取 model_id、tenant/project/user、platform_trace_id；observability 默认列表仍含业务键 | 平台 graph factory 和 tracing wrapper 负责；核心只生成通用运行诊断 |
| tenant/project 身份 | JWT 业务校验已有平台实现，但 Principal、worker 签名快照、资源 SQL 仍固定 tenant/project | 标准 Auth 回调与任意合法 user 传递；平台实现业务授权 |
| DeepAgent workspace | 平台已有本地模块；GraphHarbor 仍保留并按包目录收录源文件 | 复用平台模块，补安全测试，最终移出核心发行包 |
| 授权机制 | 生产 middleware 调 authenticate；旧 ops 的事件桥依赖 langgraph-api 私有函数 | 在生产路由接入通用授权，不把旧私有依赖带回生产 |
| 平台 ACL | 网关已有 owner/share/project/takeover、列表过滤和补偿流程 | 保留平台数据库为权威，不能退化为 owner-only |
| 历史隔离 | 表列、Store 隐式前缀、幂等唯一索引依赖 tenant/project | 离线迁移后一次切换，新代码直接移除旧隔离逻辑 |

“只删除 model_id / tenant_id 字符串”不是完成标准。通用 metadata / context / 用户自定义字段允许携带这些名称；核心不得依据这些业务名称决定权限、调度、模型或目录路径。

## 两边职责

### 鉴权放在哪一层

runtime-service 是平台的业务应用，GraphHarbor 是承载它的通用 Agent Server。业务认证/授权代码位于 runtime-service，通过 langgraph.json 的 auth.path 注册；GraphHarbor 在请求访问资源前调用回调、执行返回的过滤条件或拒绝，并把已认证用户传给 graph。两者可以运行在同一个进程，不意味着业务规则属于 GraphHarbor 核心包。

认证回答“你是谁”，授权回答“你能对哪个资源做什么”。platform-api 负责平台登录、委托签发及权限数据；runtime-service 负责校验委托、实现平台资源授权；GraphHarbor 负责标准机制及执行结果。只在 graph factory 内检查权限不够，因为读取/删除 thread、历史、Store 等请求未必运行 graph。

官方依据：[自定义认证](https://docs.langchain.com/langsmith/custom-auth)、[认证与授权](https://docs.langchain.com/langsmith/auth#authorization)。上述接口属于 Agent Server，不应与单独使用 LangGraph 图执行库混淆。

### 平台 ACL 是什么

ACL = Access Control List（访问控制列表），在本项目指“某个用户对某个会话拥有哪些操作权限”的平台规则与记录，包括所有者、私有/项目可见性、共享读写或审批权限、限时管理员接管。它已经存在于 platform-api 的 runtime_gateway/application/thread_access.py 和相关数据库记录中，本次不是新增一套权限产品。

例如：同属项目 P 的甲和乙，不代表乙能读取甲的私有会话；甲仅分享读取权限，也不代表乙能继续运行、审批或删除。tenant/project 隔离无法表达这些对象级规则。ACL 数据和判断留在平台，GraphHarbor 不建 ACL 业务表。

### 业务项目适配清单

下表是平台必须交付的改动；业务参数从核心删除后，仍可作为应用自定义 user/context/metadata 传递，但核心不解析其语义。

| 平台位置（仓库根下） | 要做的适配 | 对应任务 |
|---|---|---|
| apps/runtime-service/src/runtime_service/auth/platform.py、runtime/auth.py、应用 langgraph.json | 注册标准 Auth；校验 JWT；补各资源 operation/目标/项目/ACL 授权，统一所有 handler 的公共拒绝规则 | A03 |
| apps/platform-api/src/platform_api/modules/runtime_gateway/presentation/http.py、application/service.py | 签发匹配实际操作的委托；修正创建、fork、失败补偿；保留网关现有平台权限校验 | A03—A04 |
| 同模块 application/thread_access.py 及内部授权端点 | 复用既有 ACL 权威数据；给 runtime-service 提供受认证的权限查询；不把平台回调写进 GraphHarbor | A03 |
| apps/runtime-service/src/runtime_service/services/ 下现役 graph factories、runtime 模型解析模块 | 从标准 langgraph_auth_user / Runtime.user 解析平台事实；模型、BYOK、工具限制由平台执行，删除对核心旧业务上下文出口的读取 | A05、B02 |
| apps/platform-api/src/platform_api/core/security/tokens.py；runtime-service 的 observability/langfuse.py、otel.py | 统一可信关联字段签发；业务 model/platform trace 由平台生成，不再依赖 worker 注入 | B01—B02 |
| apps/runtime-service/src/runtime_service/workspace/、runtime/resource_bindings.py | 使用已有本地 workspace；保留路径安全与资源绑定校验；移除 GraphHarbor workspace 导入 | C01—C02 |
| 实际 Store 消费者、平台维护清理步骤、runtime-service 依赖锁定 | 平台管理 namespace；维护窗口备份并清理旧运行历史，Store 先盘点；切换到新 GraphHarbor，不保留旧包兼容 | D01—D05 |

以上同时覆盖 graph 执行和非 graph 资源请求。平台前端调用入口原则上仍走现有 platform-api；涉及可见权限变化时回归验证，不默认重写前端。

| 能力 | GraphHarbor | platform-api / runtime-service |
|---|---|---|
| 认证 | 加载 auth handler、标准 user/permissions、失败处理 | JWT 签发/校验、角色与业务 claims |
| 授权 | 按资源/action 调回调，执行过滤与受信修改 | tenant/project、ACL、委托目标/operation、撤权 |
| 后台身份 | 持久化并验证通用执行身份快照，绑定 run/thread | 解释 runtime_principal/runtime_policy，敏感业务执行时复核 |
| 执行 | graph、run、checkpoint、队列、事件、Store | 模型解析、BYOK、工具装配、审批策略 |
| 观测 | run/thread/assistant/graph、状态与性能 | model、platform trace、项目/用户、Langfuse 策略 |
| 文件 | 不内置 DeepAgent 工作目录约定 | workspace、artifact、skills、terminal 与目录隔离 |
| SDK / HTTP | 锁定版本的通用协议兼容 | 通过既有 runtime_gateway 调用；业务路由留在平台 |

## 推荐实施顺序

| 阶段 | 交付和退出条件 | 任务位置 |
|---|---|---|
| P0 基线 | 挂载路由→授权事件矩阵、官方最小 probe、消费者/数据清单 | A01、D01 |
| P1 授权与平台接入 | 新实现中标准回调生效、ACL 不退化，不上线半成品 | A02—A04 |
| P2 通用身份与业务迁移 | v2 执行快照，平台模型/trace 完整；workspace 独立 | A05、B01—B03、C01—C02 |
| P3 数据切换 | 旧运行历史备份/清理、域化幂等键、Store 契约核对；可演练回退 | D02—D05 |
| P4 收口 | 无存量旧任务与旧客户端依赖，删除旧运行逻辑，wheel 与跨项目 Final 通过 | A06、B04、C03、D06 |

开发按阶段验证，部署为一次整体切换；不得上线已删旧隔离而新授权尚未验证的版本。trace/workspace 可先完成独立验收，但不能据此宣布整体解耦完成。

## 与旧项目的关系

- [20260909 边界分离](../20260909-graphharbor-business-boundary-separation/README.md)：部分提取已经落地，但其 FINAL_SUMMARY 的“移除所有业务耦合”不符合当前源码。本计划承接剩余工作。
- 旧专题 04 提议新建 dispatch 层；平台实际已有 runtime_gateway，本次不重复建设。
- [checkpoint 安全](../20260924-checkpoint-mutation-safety/README.md) 和 [协议验证](../20260925-agent-server-contract-validation/README.md) 的已修内容作为回归约束，不在本专项重复修复。授权迁移必须保留 prune / rollback 已授权资源范围。
- 平台 20260922 跨服务治理中的 trace/JWT 方案是独立待评审提案。本次以现行源代码为准，不顺带实施全量 W3C trace 或恢复已退役服务。

## 评审决策

用户已确定“不保留旧业务兼容”的方向；以下具体授权和数据方案仍待评审：

1. 通用服务器不定义 tenant/project；业务字段及 ACL 留在平台，生产资源访问接入标准 Auth。
2. 平台授权复用现有 ACL 与内部签名回调模式；不复制 ACL 数据库、不建立新 dispatch 服务。
3. 内部执行快照与外部短期 JWT 分离，采用接收任务时的受信身份快照；新增动作重新授权。
4. 用户已批准维护切换窗口直接删除历史 GraphHarbor 运行数据，不做旧业务字段回填；迁移只移除旧列/索引，运行数据清理由明确的维护操作执行。Store 物理 namespace 由平台管理，核心不解析。
5. 幂等键通用化与 Store 对外 namespace 属显式契约切换，不得冒充“无差异内部重构”。

评审记录：2026-09-25，用户明确“我同意，按照规划开始实施”，批准 01—04 按彻底解耦方案实施；后续明确维护窗口历史运行数据可直接删除。两个仓库同步适配；不自动发布或切换生产环境。

## 本轮验证边界

官方依据（在线文档可能超前于锁定版本；行为以 0.13.0 / 0.4.3 的可执行差分为准）：

- `https://docs.langchain.com/langsmith/auth`：认证返回自定义 user，授权按资源/action 选最具体处理器。
- `https://docs.langchain.com/langsmith/resource-auth`：metadata 注入与资源过滤。
- `https://docs.langchain.com/langsmith/store-auth`：Store namespace 隔离。
- `https://reference.langchain.com/python/langgraph-sdk/auth/Auth/on`：Auth 回调 API；本地参照 `_get_handler` 与 SDK types 已交叉阅读。

**2026-09-25 候选验证：** 隔离 PostgreSQL 17 上 GraphHarbor runtime-pg 全组 148 passed、18 skipped；langhost 全组另起进程 58 passed。双 wheel/sdist 构建并安装到临时目录，平台三份真实配置各加载 65 条路由；平台 Runtime Service 定向 164 passed、Platform API ACL/gateway 51 passed、3 skipped、335 subtests passed，Web typecheck 通过。临时 platform-api 与候选包 Runtime 的真实 HTTP 链路完成创建、私有拒绝、共享只读、写入拒绝与撤权即时生效。两个临时服务已停止。验证使用可丢弃 SQLite/PG17 数据，不涉及真实模型、生产 API、发布或切换；剩余准入见各专题。

**2026-09-25 追加验证：** 平台 ACL 回查现在按已签服务账号 `credential_id` 复核 token 状态、期限和项目 grant；Runtime operator 的只读委托可刷新 Graph 目录。平台 ACL/gateway 定向 43 tests（3 skipped）、Runtime Auth 46 passed，隔离平台 API + 候选 Runtime + Web 的治理浏览器文件 10 passed，含服务账号 grant/token 撤销、管理员接管及子资源拒绝。临时服务均已停止；浏览器文件创建/预览/下载、业务 run/HITL 和官方全入口 Auth 差分仍未执行。

**2026-09-25 迁移前复核：** 隔离 PG17 runtime-pg 全组 149 passed、18 skipped，官方 0.13.0 的 identity-only Auth/Thread/`runs/wait`/HITL/SSE 子集差分通过。当时平台 2142 数据库缺少 Alembic `20260925_0005` 的 Thread ACL 列，浏览器 Thread 创建 500 已定位于平台 SQL，未进入 Runtime；平台 API 新增启动时旧 schema 拒绝检查，临时 SQLite 回归通过。平台现役源码未发现 GraphHarbor Store 消费者，外部消费者未盘点。全入口差分、业务执行与文件正向验收仍未完成。

**2026-09-25 本机切换进展：** 上段是迁移前诊断。本机两库已停写并分别归档到仅本机可读的 `/tmp/graphharbor-boundary-20260925-prewipe-runtime.dump` 和 `/tmp/graphharbor-boundary-20260925-prewipe-platform.dump`；`pg_restore --list` 已核对归档目录，完整恢复尚未演练。旧 Runtime 运行历史及平台 Thread ACL/run submission ledger 已按明确表名清理，Dear memory/skills 保留。Runtime 已升至 `008_remove_business_scope`，平台已升至 `20260925_0005`；现役 Runtime 库 11 MB，Thread/Run/Event 均为 0。候选 wheel 安装到平台 Runtime 虚拟环境，本机栈健康；当前必须以 `UV_NO_SYNC=1` 启动，普通 `uv run --frozen` 会按平台锁文件重新安装旧版 PyPI wheel。这个本机启动限制和剩余验收见专题 04。

## 暂停点与恢复入口

2026-09-25 暂停快照：本机 `graphharbor_acceptance` 为 `008_remove_business_scope`，`platform_api` 为 `20260925_0005`；Thread/Run/Event 及平台 ACL/run requests 已清零，Dear memory/skills 保留。两份 0600 备份仍在上述 `/tmp` 路径，仅核对归档目录，未完整恢复。本轮检查时 8123 `/ready` 健康，候选平台栈采用已安装 wheel + `UV_NO_SYNC=1`；这些进程和临时文件的后续存在性必须重新核对，不能照抄为未来的验收结果。

完成事件保留专项后，从[专题 04 的 D05/D06 与 V-D06—D08](04-data-migration-and-cutover.md)继续：先解决平台锁文件指向旧 PyPI wheel 的可重复安装问题并做两库备份完整恢复；再以新数据跑业务 Run/SSE/HITL、文件创建/预览/下载及 workspace 恢复；最后完成官方全入口 Auth/API/SSE 差分、拒绝副作用、打包和回退门禁。身份、模型/trace、workspace 各专题未勾选的 Final 项照原文逐项核对。不能因为本机库已清理、服务健康或事件保留项目完成就宣称业务边界专项 `done`；无用户明确发布指令不提交、推送或发布。
