# 01 身份、标准授权与平台 ACL

## 目标

让仅提供 identity 的自定义认证应用也可安全运行；GraphHarbor 不要求 tenant/project/role。平台现有跨项目隔离、私有与共享会话、takeover、审批权限仍有效，读写、SSE、历史和后台任务均不能绕过授权。

## 方案设计

### 源码事实

本专题路径以仓库根为起点。G = GraphHarbor，P = ai-agent-platform。

- G `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`：Principal 固定 tenant/project；from_auth_user 缺字段时填 __default；签名上下文强制 user/tenant/project/role，并特判 correlation 字段。auth_user 已有 JSON 限制、65,536 字节上限和 NaN 拒绝，必须保留。
- G `libs/langhost/src/langhost/core_api.py`：_scope、_thread_for_run、_resolve_assistant、_runtime_context 等生产调用依赖固定身份；streaming.py、protocol_api.py、mcp_transport.py、store_api.py 及 server.py 也有独立入口。
- G `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py`：Authenticated.handle_event 调用 langgraph-api 私有函数。不能把这个桥直接作为生产实现。
- P `apps/runtime-service/src/runtime_service/auth/platform.py`：JWT 已由平台 authenticate 校验；现有全局 deny 回调对 ctx.user 使用 isinstance(dict)，需兼容 ProxyUser；拒绝 operation 列表未覆盖 dear-memory。
- P `apps/platform-api/src/platform_api/modules/runtime_gateway/application/thread_access.py` 已实现 ACL；service.py 的 _visible_threads 按授权 ID 分批查询，create_thread 已改为先预留 ACL，再用绑定 UUID 的 `thread-create` 委托创建 Runtime thread。
- 本地参照 langgraph-api/auth/custom.py 的 _get_handler **选一个最具体处理器**：resource+action → resource → action → global；同匹配使用最后注册者。其 handle_event 注释写“all handlers”，实际实现不是链式执行。增加资源级 handler 会遮蔽全局 handler。

### 1. GraphHarbor 通用 Auth

保留 SDK Auth、标准 identity/permissions 与 JSON-safe custom user。核心不解析 runtime_principal、runtime_scope、runtime_policy；这些只是平台 user 的不透明扩展。

在现有 auth.py 增加最小事件分派和结果处理，保持 authenticate 与 authorize 分工；只实现目标官方 Auth 接入契约，不为旧自定义 callable 接口新增兼容层。生产环境不依赖 langgraph-api，也不新增独立插件框架。SDK 私有注册表如不可避免，访问集中在一个小适配函数，并对锁定版本执行契约测试。

严格区分：None/True 允许；False 拒绝；dict 为 metadata filter；无匹配处理器沿参照行为。异常、未知返回类型、value 修改、User/Mapping 访问、过滤运算符均做差分，不以 bool(result) 混淆 False 和空过滤器。已配置 Auth 加载失败必须拒绝启动或拒绝访问，不能回退匿名。

复用 ops.py 已有纯过滤逻辑前先核对参照语义，不整体搬运旧运行栈。授权与用户查询条件取交集；先过滤再分页/count。SQL 能下推则下推；复杂过滤不得只对取出的当前页过滤。

| 生产入口 | 授权要求 |
|---|---|
| assistants CRUD/search/count/versions/schema/graph | 按参照映射 assistants 事件；内置 graph assistant 的只读可见性显式确认，不把无归属行全开放 |
| threads CRUD/copy/prune/state/history/checkpoint | 单对象和批量均逐目标授权；copy 同时授权源和新资源；mutation 只接收授权后集合 |
| runs create/stream/wait/batch/stateless | threads.create_run 与依赖资源授权；隐式新建 thread 也执行创建规则 |
| runs get/list/join/cancel/delete | 继承目标 thread 权限并核对 run↔thread；无 thread 的 run 按参照行为单独覆盖 |
| SSE / stream 恢复 / 协议命令 | 首次和重连均授权，未授权前不得发送历史事件；不得只保护 REST |
| crons | CRUD 和目标资源授权；后台触发身份不能默认为系统全权 |
| Store | 独立 namespace 授权，见 04，不套 metadata filter |
| MCP / 别名 / 自定义业务路由 | 实际挂载入口逐一列入；MCP 调 graph 不得绕过应用策略；平台自定义路由保留其独立授权 |

表为覆盖清单，不宣称所有行都已经符合参照。P0 必须列出实际 method/path→handler→resource/action→对象范围→对应测试；参照未支持的扩展单列，不能发明官方事件名。

### 2026-09-25 生产挂载路由核对

按 `langhost/server.py:create_app` 实际挂载合并同类路径。GET `/assistants` 与 GET `/threads` 的包装函数调用核心 search handler。MCP、protocol commands/events 为 GraphHarbor 扩展，不能冒充官方 Auth 事件。

| method / path 组 | handler | Auth resource.action / 对象范围 |
|---|---|---|
| GET `/ok` `/live` `/ready` `/info` `/openapi.json` `/docs` `/metrics` | server 公共处理器 | 无资源事件，middleware 公开范围单独核对 |
| GET `/assistants`，POST `/assistants/search` `/assistants/count` | assistants_search/count | assistants.search，metadata SQL filter 先于分页/count |
| POST `/assistants` | assistants_create | assistants.create；重复 ID 另查 assistants.read |
| GET `/assistants/{id}` `/graph` `/schemas` `/subgraphs`，POST `/versions` | assistants_get/graph/schemas/subgraphs/versions | assistants.read，目标 ID |
| PATCH/DELETE `/assistants/{id}`，POST `/latest` | assistants_update/delete/latest | assistants.update/delete，目标 ID |
| GET `/threads`，POST `/threads/search` `/threads/count` | threads_search/count | threads.search，metadata SQL filter 先于分页/count |
| POST `/threads`，GET/PATCH/DELETE `/threads/{id}` | threads_create/get/update/delete | threads.create/read/update/delete，目标 ID |
| POST `/threads/{id}/copy` `/threads/prune` | threads_copy/prune | 源 read + 目标 create；prune 逐目标 delete/update |
| GET/POST/PATCH `/threads/{id}/state*` `/history` | threads_state/update_state/history | threads.read/update，目标 ID |
| POST `/runs` `/runs/stream` `/runs/wait` `/runs/batch` 与 `/threads/{id}/runs*` 创建型路径 | runs_create 与包装处理器 | threads.create_run + assistants.read；隐式 thread 还需 threads.create |
| GET/DELETE `/threads/{id}/runs/{run_id}`，GET `/threads/{id}/runs` `/join` | runs_get/delete/list/join | threads.read/delete/search，run 绑定目标 thread |
| POST `/threads/{id}/runs/{run_id}/cancel` `/runs/cancel` | runs_cancel/cancel_many | threads.update，逐 run 核对 |
| GET `/runs/{run_id}/stream` `/threads/{id}/runs/{run_id}/stream` `/threads/{id}/stream` | streaming | threads.read，首连、重放、实时发送与心跳复核 |
| POST `/runs/crons*`，PATCH/GET/DELETE `/runs/crons/{id}` | cron handlers | crons.create/search/update/read/delete；创建还读 assistant/thread |
| PUT/GET/DELETE `/store/items`，POST `/store/items/search` `/store/namespaces` | store_api | store.put/get/delete/search/list_namespaces，namespace 由应用授权 |
| POST `/threads/{id}/commands` `/stream/events` | protocol_api | run.start→threads.create_run；input.respond→threads.update/create_run；事件流→threads.read |
| `/mcp` tools/call；`/` 自定义 app | mcp_transport；应用 app | MCP 调用 assistants.read + threads.create_run，无法应用 filter 时拒绝；自定义 app 自行授权 |

静态路由清单不替代官方动态 Auth 差分，也不证明所有拒绝路径已覆盖。

### 2. 平台授权适配器

继续使用 `runtime_service/auth/platform.py` 和 `runtime/auth.py`。每个资源处理器显式调用一个公共平台 guard，核对 operation、tenant/project、assistant/thread、context_hash 与所需权限；不能依赖全局 handler 自动叠加。

平台保留真实 user identity，不能改成 project service identity 来绕开多租户。角色、模型与工具策略只在平台解释。read 委托不得创建/更新/删除；run-create 不得用于 workspace、terminal、memory 或其他会话；自定义操作令牌拒绝所有原生资源操作。现有网关用 read 委托执行的写入必须同步改为目标绑定的相应操作委托。

metadata 中 tenant_id/project_id 是**平台保留业务键**：创建时由认证事实写入；更新时拒绝改动或覆盖为原值；调用者同名字段不能提供权限。当前已有 project_id，优先复用，不新增一套复杂嵌套 schema。旧运行历史按 04 的维护窗口方案清理，不从旧隔离列回填。

ACL 仍由 platform-api 的 thread_access.get / require_action / visible_records 决定。推荐在现有 runtime_gateway 内新增一个小型内部批量授权 HTTP 端点，复用现有 memory-authorization 的 HMAC/时间窗/httpx 模式，不能把“个人记忆允许”直接当成“会话允许”。

- 请求含签名绑定的实际用户、项目、动作、目标 ID 集合、请求时间；对规范化完整请求体及路由用途签名，避免签名被换 action/target 重放使用。最多 100 个 ID，与现有网关批次一致。
- 平台从自身数据重建权限上下文，不接受 runtime 自报角色作为最终授权依据；查询直接访问 ACL/成员关系服务，不反向调用 runtime，避免环路。
- 返回仅包含授权结果/ID，运行端同时施加 metadata scope。超时/非法签名/异常拒绝或返回可重试服务错误，绝不放行；复用现有 3 秒 timeout，首次实现不加跨请求权限缓存。
- 单对象访问按资源/action→业务动作映射检查，保留 share、approve、comment、delete、限时 takeover 差异及审计。
- 平台原生 threads search 必须给出有界 ids（现有网关已如此）；适配器批量核对这些 ID，任一越权则拒绝整个内部批次。未携带 IDs 的平台直连查询拒绝；平台列表和精确 count 继续走现有 _visible_threads。这是平台策略，不是 GraphHarbor 通用限制。
- 需对 ids、value 修改的实际消费做锁定版本探测；不要依赖未核实的“改 value.ids 自动过滤”。推荐验证原请求 IDs 并返回平台 metadata filter，GraphHarbor 使用原请求 IDs 的 SQL 限制，避免新增 ID 过滤协议。
- 无线程的 Store / stateless run / cron 如当前平台无产品授权入口，平台适配器显式拒绝；通用服务器能力和独立 fixture 仍保留。启用它们必须补业务授权与自动执行凭据策略，不能以未使用当成已支持。

### 3. 创建与失败补偿

直接启用实时 ACL 回调会打断“先 Runtime 创建、后注册 ACL”。推荐复用已有 register/remove：

1. 网关确定 UUID，检查项目创建权限，**先写 ACL 记录**，再签发仅绑定该 UUID 的创建委托。
2. Runtime 创建回调检查平台 ACL、创建 operation，写入可信项目 metadata，禁止覆盖现有 thread（if_exists=raise）。
3. 上游明确未创建且无并发资源时才清理预留 ACL。超时/响应丢失属于结果未知，保留记录并按原 UUID 对账，禁止立刻删除 ACL。
4. 补偿删除使用相同 UUID 的受限删除委托；先确认 Runtime 删除，再移除 ACL。孤立 ACL 不是放宽权限的理由。
5. thread copy / fork / 隐式创建执行相同流程；旧 read 委托没有“清理特权”。优先利用现有记录和日志对账；只有并发恢复证明需要时才增加 provisioning 状态。

### 4. API → worker 通用身份快照

建议 v2 封装只包含版本、受信 auth_user/permissions、issued_at/accepted_at、issuer/audience、run_id/thread_id，以及用于验证完整性的签名。JSON 验证、长度上限、签名比较、绑定、保留内部字段防覆盖均保留。

- 外部 JWT 在 API 接受新动作时校验；内部快照是已持久接收任务的身份凭据，不是可直接请求服务器的 bearer token。
- worker 不再强制 tenant/project/role，也不因为外部 JWT 在排队中自然过期而重新拒绝已接收任务。v2 有效性依据受信接收记录、目标绑定和执行生命周期；不通过“忽略旧 v1 的 exp”偷偷改变旧语义。
- 恢复/重试仅使用该 run 的快照；新的 resume/写入/读取重新认证授权。业务敏感副作用的撤权复核继续由平台模型、消息/记忆授权等入口负责。
- cron 每次触发属于新的执行授权，不使用任意过期用户 token 无限续权。当前平台无受支持周期任务授权时关闭该产品入口；通用 cron 按锁定版本及独立应用策略验收。
- graph_executor 保留 langgraph_auth_user 与 Runtime(ServerInfo.user) 标准入口；废弃 __graphharbor_runtime_policy 等旧平台出口前先查消费者。
- worker 当前对 configurable 中 tenant/project/user/role/permissions 的固定清洗也需迁到平台输入策略；核心只保护自身保留键与身份入口。用户自定义同名普通 config 不能变成认证身份，但通用应用也不应因字段名称被核心无条件删除。
- 新版本只读写通用 v2，删除旧 v1 运行分支。停写并停止旧 worker 后，对允许保留执行的任务离线转换或重新授权；无法确认身份的任务不自动启动，见 04。不实现双格式读写或混跑。

## 任务拆分

| ID | 改动内容 / 代码位置 | 预期结果 | 验证项 | 状态 |
|---|---|---|---|---|
| A01 | G server/core_api/protocol_api/streaming/mcp_transport/store_api 挂载清单；锁定官方 Auth probe | 明确每个入口的事件和边界 | V-A01、V-A02 | 部分完成：实际挂载核心路由与扩展入口已分组映射；官方动态 Auth probe 未完成 |
| A02 | G auth.py、生产 handlers；提取可复用纯 filter 逻辑 | 新授权在完整候选版本生效；固定 scope 直接移除，联合验证后部署 | V-A01—A04 | 部分完成：REST/SSE Auth filter 与撤权检查已接入；MCP tools/call 现先授权，缺身份或无法应用 filter 则拒绝；cron/Store/协议 run.start 拒绝无副作用用例通过。全入口动态差分仍待验，禁止发布 |
| A03 | P auth/platform.py、runtime/auth.py、gateway presentation + application；内部授权端点 | operation/目标校验、ACL 复核、可信 metadata | V-A03—A06 | 部分完成：内部批量 ACL 端点与线程回查已完成；创建回查限 pending 且 owner/project 匹配；Runtime Auth 限制委托 operation 并 fail-closed。服务账号 token/grant 撤销、浏览器共享/接管与 operator 目录刷新通过。完整入口授权差分和跨资源直连矩阵未完成 |
| A04 | P gateway create_thread/copy/补偿与 thread_access | 创建、超时、对账不发生授权空窗 | V-A06 | 部分完成：先 ACL 预留；`thread-create` UUID 绑定且不伪造 assistant；明确 4xx 清理，5xx/504 保留 pending 预留；用户可对账自身 pending 线程，Runtime 用受限 reconcile 委托读取并置 ready。探测 404 时 504 错误扩展现在返回 UUID 与 reconcile 路径；平台服务层 22 项通过、3 项跳过。其他故障注入和完整联合验收未完成 |
| A05 | G auth.py/core_api/graph_executor/production_worker；P graph factories | v2 传递任意合法自定义 user；无业务特判 | V-A07—A08 | 部分完成：v2 accepted_at 快照、run/thread 绑定、opaque auth_user、MCP 同步；平台敏感策略与排队/重启全链路待验 |
| A06 | 完成 04 后删除固定 Principal/scope/旧身份分支及陈旧测试假设 | 核心运行链不依赖固定身份结构 | V-A09 + Final | 部分完成：固定 Principal 字段、SQL 列与查询已删除；旧 helper 和测试仍需收口，Final 未完成 |

## 验证要求与记录

- [ ] V-A01：同一 Auth fixture 跑官方 0.13.0 与生产 GraphHarbor；覆盖 handler 优先级/覆盖、None/True/False/dict、错误返回、异常、value 修改、ProxyUser、认证参数；保存事件调用记录与响应差分。
- [ ] V-A02：挂载路由全部映射；使用仅 identity 的应用及另一种自定义 org/team 身份，证明核心不要求平台字段。生产环境排除 langgraph-api 后仍通过。
- [ ] V-A03：tenant/project 跨域、同项目不同用户、shared read-only、approve/comment、takeover 到期及撤权；gateway 与直连 runtime 双路径均验证。
- [ ] V-A04：search/count/分页与空 IDs，单对象、批量取消、prune keep_latest、rollback、copy/fork、SSE 首连/重连、MCP、stateless、cron；拒绝时无状态或 checkpoint 副作用。
- [ ] V-A05：用户伪造 metadata/config/context/auth_user、模型权限、内部签名快照均不能提权；回调超时/重放/改 action、过大 batch 均拒绝。
- [ ] V-A06：创建成功/注册失败/Runtime 明确失败/响应丢失/重复创建/补偿失败；未知结果可按 UUID 对账，既无裸资源也不误删已有会话。
- [ ] V-A07：身份 JSON 往返、65,536 字节边界、非 JSON、NaN、签名篡改、跨 run/thread、issuer/audience；公开响应/SSE/日志不泄漏快照或敏感 claims。
- [ ] V-A08：排队超过 300 秒、after_seconds、worker 崩溃重试、重启、HITL 恢复、外部 JWT 到期和撤权分别验证，不混为一种过期策略。
- [ ] V-A09：无认证本地模式、认证生产模式、自定义应用均通过；失败加载不能退回匿名，业务策略字符串仅在平台/测试/迁移文件内出现。

复用测试入口：G 的 test_production_contract.py、test_rest_contract.py、test_checkpoint_mutation_safety.py；P 的 runtime/test_auth.py、test_platform_auth.py、integration/test_agent_server_auth.py；platform-api/tests/test_thread_access_policy.py、test_runtime_gateway_http_matrix.py、test_runtime_delegation.py。扩展现有测试，不另建测试框架。陈旧测试中引用已不存在的 JWT 类须依据目标契约修正，不能删除安全场景以换取通过。

**2026-09-25 实施记录：** v2 快照改为 `v=2 + accepted_at + auth_user + permissions + run/thread`，拒绝旧 `iat`/业务顶层字段；Principal 允许 identity-only；REST、worker、MCP 不再注入旧 `__graphharbor_runtime_context`/runtime policy。平台新增线程 ACL 批量回查端点，Runtime Auth 依据已验签 delegation facts 发起短时 HMAC 回查；平台端点 2 项、平台既有 unittest 20 项通过。平台创建链路已改为 ACL 预留和 UUID 绑定的 `thread-create` 委托，未绑定 graph 时不再伪造 assistant；成功创建置为 ready，明确拒绝清理，5xx/504 保留 pending。Runtime Auth 现正确处理 GraphHarbor 传给回调的 UUID 对象。`tests.test_thread_acl` 和 `tests.test_runtime_delegation` 共 31 项（3 skipped）通过，Runtime Auth 15 项通过；Alembic head 为 `20260925_0005`。后台对账仍缺少受限读取委托，完整入口矩阵和旧 SQL 列删除仍未完成，不能宣称 A03/A06 完成。

**2026-09-25 授权链路续做：** GraphHarbor thread/run SSE 在首次读取及历史回放、实时发送和心跳周期复核资源授权；协议事件流同样在回放与实时阶段复核 thread 授权。协议 latest/idempotency run 查询绑定 thread 并调用 `_authorized_run`；`input.respond` 在消费中断状态前要求 update 权限。Runtime Auth 对非 thread 资源 fail-closed，仅允许委托绑定的 assistant 读取（含 GraphHarbor graph UUID），明确拒绝 cron/Store/其他资源；read 委托禁止 thread 写入与执行。验证：GraphHarbor `uv run ruff check libs/langhost/src/langhost/streaming.py libs/langhost/src/langhost/protocol_api.py` 通过，授权测试 14 passed、1 skipped；平台 Runtime Service 授权测试 23 passed；两仓 `git diff --check` 通过。未覆盖 SSE 撤权并发集成、实际 ACL 服务联合调用和 PostgreSQL 资源矩阵。核心 REST/管理接口仍有旧 SQL scope，故 SQL 列尚不能删除。

**2026-09-25 创建授权收口：** 平台 `thread_access.pending_owner` 将 Runtime `create` 回查限制为仍 pending、project 与 owner 均匹配的 UUID 预留；ready ACL 不再接受创建委托重放。由于 platform-api 虚拟环境缺少 pytest，使用 `python -m unittest tests.test_thread_acl -q` 验证：15 项通过、3 项跳过；直接执行 `tests/test_runtime_thread_authorization.py` 的 3 个断言检查通过。相关文件 `compileall` 和平台 `git diff --check` 通过；平台 Ruff 未安装，未运行。Runtime Service `tests/runtime/test_platform_auth.py` 23 passed。后台只读委托和可调度对账机制仍未实现。

**2026-09-25 联合定向复测：** Runtime Service `tests/runtime/test_platform_auth.py`、`test_auth.py`：43 passed；Platform API `test_thread_acl.py`、`test_runtime_gateway_http_matrix.py`、`test_runtime_delegation.py`：36 passed、3 skipped、335 subtests passed。测试使用 `apps/runtime-service/.venv` 并从 `apps/platform-api` 设置 `PYTHONPATH=src`。GraphHarbor PostgreSQL 17 隔离库 `graphharbor_boundary_pg17` 上 `test_production_contract.py`：65 passed、4 skipped；完整 REST/cron/store 路由矩阵和旧 SQL scope 尚未完成。

**2026-09-25 未知创建结果修复：** `RuntimeGatewayService.create_thread` 在上游创建超时且受限 reconcile 查询返回 404 时，保留原 504，并在 `error.extra` 添加 `thread_id` 与 `/api/langgraph/threads/{thread_id}/reconcile`；pending ACL 不清理，其他探测错误不附加对账信息。验证：`PYTHONPATH=src ../runtime-service/.venv/bin/python -m unittest tests.test_thread_acl -q`，22 项通过、3 项跳过。错误 payload 通用 handler 会保留 `extra`，但本轮未单独执行真实 HTTP 客户端断言，也未完成 Web 端恢复交互验收。

## 状态

**2026-09-25 候选最终验证：** GraphHarbor runtime-pg 全组在隔离 PG17 串行执行 148 passed、18 skipped；langhost 全组另起进程 58 passed；mypy 37 个源文件与相关 Ruff 通过。安装双包候选后，平台 Runtime Service 定向 164 passed，Platform API ACL/gateway 51 passed、3 skipped、335 subtests passed。临时平台 API `127.0.0.1:2342` 和候选 Runtime `127.0.0.1:8323` 通过真实 HTTP 创建 Thread：同项目另一用户私有读取 403，共享 read/comment 后读取 200、修改 403，撤权后读取 403，所有者仍可读取 200；临时服务已停止。此前同进程数据库测试会被测试自身改写的 `DATABASE_URI` 干扰，最终分进程运行均通过。官方全入口动态 Auth 差分及 SSE 撤权并发仍未执行。

**2026-09-25 最终复跑：** 在两个独立进程分别运行 `DATABASE_URI=postgresql+asyncpg://lijiaxin@localhost:5432/graphharbor_boundary_pg17 BOUNDARY_TEST_DATABASE_URI=postgresql+asyncpg://lijiaxin@localhost:5432/graphharbor_boundary_pg17 REDIS_URI=redis://localhost:6379/0 uv run pytest -q libs/langgraph-runtime-pg/tests` 与同环境的 `uv run pytest -q libs/langhost/tests`，结果分别为 148 passed、18 skipped 和 58 passed。首次复跑发现旧认证测试用普通 `ValueError` 表示缺少凭据，当前契约将未声明状态的异常脱敏为 500；测试 fixture 改用 `Auth.exceptions.HTTPException(401)` 后定向和全组均通过，相关 Ruff 通过。平台 ACL 当前权限回查单测 1 passed，平台 `scripts/check_docs.py` 通过。

**2026-09-25 实际委托链复核：** 在候选 wheel Runtime + 临时 SQLite 平台服务上，owner 创建 Thread 为 200；管理员无 takeover 删除为 403；授予限时 takeover 后管理员读取为 200、通过 `thread-delete` 委托删除为 200。另验证 owner 删除可作为 E2E 失败清理路径。修复 platform browser governance fixture：服务 teardown 时 ACL HTTP 监听已关闭，不能再从 lifespan teardown 调 Runtime；fixture 改为检查遗留 ACL 记录，测试必须在平台仍在线时经网关清理。临时服务已停止。

**2026-09-25 服务账号与目录追加验证：** 平台委托签入服务账号 `credential_id`，Runtime 只将已验签值放入 HMAC ACL 回查；平台按 token 所属账号、状态、到期时间和项目 grant 重建 actor。SQLite 单测覆盖私有拒绝、grant 与项目共享同时满足后放行、冒用其他账号 token、grant 撤销、token 过期及撤销后拒绝。仅平台 operator/superadmin 的 read 委托允许 Graph 目录搜索，真实 `/api/runtime/graphs/refresh` 为 200。平台 ACL/gateway 43 tests（3 skipped）、Runtime Auth 46 passed；候选 Runtime + 平台 API + Web 的治理浏览器文件 10 passed，含私有/共享/接管、服务账号撤权、子资源拒绝与浏览器身份失效。旧版用户创建两步 E2E 与当前一次提交页面不符，已按现行流程修正并在全文件复跑通过。文件正向操作、业务 run/HITL 与官方全入口动态差分仍缺。

**2026-09-25 事件保留候选复核：** 独立 PG17 双库和候选双 wheel 联调中，平台超级管理员创建项目后 Graph 搜索曾返回 403：网关把项目角色优先签入委托，Runtime 的全局目录搜索只接受平台 operator/superadmin。平台网关现优先签发已有平台级角色，普通项目角色仍不升权；`test_runtime_gateway_http_matrix.py` 整文件 3 tests 通过，错误级 Ruff 通过。隔离 HTTP 重测 `/api/langgraph/graphs/search` 200（4 图）、Thread 创建 200；本地启动脚本也补齐 `PLATFORM_THREAD_AUTHORIZATION_URL`，避免创建 Thread 时 500。平台 Web/业务 Run/HITL 尚未因此验收。

**2026-09-25 官方差分与平台 schema 复核：** 隔离 API/worker 与官方 `langgraph dev` 0.13.0 对同一 identity-only Auth fixture 的 Thread 过滤/拒绝、`runs/wait`、HITL 和 SSE 错误事件差分通过；这不是全入口矩阵，V-A01/V-A04 仍未勾选。现有平台 2142 的 Thread search/count/create 返回 500；平台日志确认 `thread_access.provisioning_status` 列缺失，创建请求尚未抵达 Runtime。平台 API 启动新增 schema 准入检查，缺少 `20260925_0005` 所需列时明确拒绝启动；临时 SQLite 旧 schema 回归 3 passed，Alembic 空库升级/回退 1 passed。现有平台数据库未迁移。

**2026-09-25 本机迁移后：** 上段 500 属迁移前状态。本机平台库现为 `20260925_0005`，Runtime 库为 `008_remove_business_scope`；8123 `/ready` 与 2142 `/_system/health` 健康。旧 Thread ACL 与 run submission ledger 已协调清理，后续新建 Thread 仍需经平台 ACL 正向联测。官方全入口矩阵和业务 run/HITL 尚未完成。

partial：A02/A03/A04/A05/A06 有阶段实现，A01 官方全入口 Auth 差分和 Final 尚未完成；已完成的 Thread HTTP 链路不能代替所有资源与 SSE 拒绝路径，仍禁止生产切换。

2026-09-25 隔离治理浏览器补验：将运行中 Runtime API 的 `PLATFORM_THREAD_AUTHORIZATION_URL` 临时指向隔离治理平台 12142 后，`RUN_LOCAL_GOVERNANCE_E2E=1 ... playwright test e2e/platform-access-governance.spec.ts --workers=1` 10 项均通过（29.4 秒），覆盖私有/共享/接管/撤权、服务账号 grant/token、子资源拒绝和成员移除。第一次未切换回查地址时 5 项创建 Thread 返回 403，原因是隔离平台 token 被 Runtime 回查主平台 2142 拒绝；该次不计通过。测试 fixture 退出时留下 6 条临时 ACL，导致 shutdown cleanup 抛错；测试数据库在临时目录内，未触及正式平台库。完成跨资源动态差分和清理无残留仍需单独核对。

2026-09-26 联合治理复测：隔离平台 API 12142 + Runtime 8123 + Web 13000 的治理矩阵为 10 passed；fixture `thread_access` 残留为 0，正式 Runtime ACL 回查地址已恢复到 2142。首轮 5 failed 的 503 是临时地址漏写 `/api/runtime/internal/thread-authorization`，不计通过。本结果不等于官方全入口 Auth 差分完成。
