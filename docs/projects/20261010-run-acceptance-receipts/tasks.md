# Run 接受回执任务清单

> 状态：**done**；13/13 个任务完成。2026-10-10 用户明确批准详细方案、开始实施和验证后本地 key 直接发布 PyPI；包含 U01–U03。正式双包、使用方锁接入与发布后联合验收完成，现役未部署，未执行 git 提交。

本文件是进度唯一事实源。新增文件/函数标为“新增”；`implementation/` 只用于实施细节和证据，不据此判定任务完成。验证编号详见 [verification.md](verification.md)，HTTP/鉴权/事务细节以 [plan.md](plan.md) 为准。

使用方仓库根目录简称 `P=../ai-agent-platform`，实际开发使用其登记 Worktree；下文 `P/` 路径均属于另一仓库。U01–U03 是使用方接线交付，不能擅自算成本仓库已有实现。

## G：引擎实施

### Task G01：实现严格请求绑定与字节摘要

- **改动内容：** 实现 v1 raw body SHA256、key SHA256、receipt_key 和严格 JSON/header 校验；明确拒绝重复 key/header、NaN、body 幂等 key、隐式建 Thread、客户端私有上下文及非法摘要。固定摘要向量，不增加 canonical JSON 库。
- **代码位置：** 新增 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_acceptance.py` → `request_digest()` / `receipt_key()`；新增 `libs/langhost/src/langhost/run_acceptance.py` → `parse_acceptance_request()`；新增 `libs/langhost/tests/test_run_acceptance_http.py`、`libs/langgraph-runtime-pg/tests/test_run_acceptance.py`。
- **预期结果：** 服务端算出的摘要等于最终传输 bytes；等价 JSON 的字节变化也触发不同摘要；非法请求在任何 Run/ledger/队列写入前拒绝，旧普通接口不被重解释为回执模式。
- **验证项：** 场景 C01–C02、C07；用 [Q01](verification.md#q01针对性新逻辑与真实pg事务) 执行新增两文件的 parser/digest 用例，固定 Unicode、空格、键/数组顺序、null/缺省、`1`/`1.0` 和重复 header 向量。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G02：固定可信 grant 与 SDK Auth 默认拒绝

- **改动内容：** 从已认证用户读取 opaque scope/credential/operation/固定 Thread-key-digest；仅新路径要求显式 Auth handler；限制 read token 的方法/路径，服务端生成 receipt marker，保留 callback metadata filters。新绑定不得使用 jti 或客户端自称身份。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py` → `Principal.from_auth_user()` / `PrincipalMiddleware.__call__()`、新增grant校验函数；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/authorization.py` → `authorize(require_handler=True)`；`libs/langhost/src/langhost/core_api.py` → `_authorize()`；`libs/langhost/tests/test_application_authorization.py` 与新增 `libs/langhost/tests/test_run_acceptance_http.py`。
- **预期结果：** 匿名/缺 grant/无 handler 403；固定只读 token 无法 POST、读 Run/state/store、取消或访问其他 Thread；普通 Auth 分派与匿名开发路径保持原行为。metadata filter 不被忽略，撤权普通回查仍拒绝。
- **验证项：** C12–C16，Q01；覆盖具体 handler 优先级、同步/异步 handler、返回 filter、伪造 marker、同 subject 不同 scope/credential、token 刷新 jti 与固定绑定不变。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G03：独立接受 ledger 与 additive migration

- **改动内容：** 新增 `RunAcceptanceRow`、主键/唯一/CHECK 与有界维护索引；无 Run/Thread FK；三类 outcome 与永久最小墓碑。迁移只建表，不回填无法证明的旧 Run，不允许 downgrade 删除 ledger。
- **代码位置：** `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py` → 新增 `RunAcceptanceRow`；新增 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/versions/013_run_acceptance_receipts.py` → `upgrade()` / `downgrade()`（实施前重新核对head）；`libs/langgraph-runtime-pg/tests/test_schema_setup.py`、新增 `libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；复用 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/database.py::connect()` / `truncate_all()` 的既有机制。
- **预期结果：** 空库与 012 库升级至单一 head；旧 Run/Thread/取消/webhook 行未变；同 binding/key 只有一槽位；Run/Thread 删除不级联删除回执；数据库拒绝不合法 outcome 组合，破坏性 downgrade 明确失败。
- **验证项：** C17–C21，Q01；在隔离库执行新 schema/CHECK/删除与 migration 用例，保留 revision、表/索引检查和旧行前后快照。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G04：Run 与回执原子提交及持久拒绝栅栏

- **改动内容：** 新接受路径用 receipt namespace 和 transaction advisory key lock；同事务写 pending Run、签名与 accepted 回执。只将结构化 Thread busy 分类为持久拒绝，封住原 key；重放不改签名、kwargs、TTL或重新入队。异常在 connect 之外转换 HTTP，避免正常退出提交半份数据。
- **代码位置：** `libs/langhost/src/langhost/core_api.py` → `runs_create()`（内部显式receipt分支，普通调用默认不启用）；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py` → `RunRepository.create()` / `RunConflictError`；新增 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_acceptance.py` → `RunAcceptanceRepository.acquire_key()` / `get()` / `record_accepted()` / `record_rejected()`；新增 `libs/langgraph-runtime-pg/tests/test_run_acceptance.py`、现有 `libs/langgraph-runtime-pg/tests/test_run_submission_concurrency.py`。
- **预期结果：** commit 前 Run/receipt 同时不可见，成功后同时可见；两 API 同 key 同 body 返回同 run_id；换 Thread/body 409；持久拒绝后 Thread 空闲或迟到原请求也不创建；ACK 丢失和 Redis 失败仍能找回原接受。
- **验证项：** C03–C11、C24–C25，Q01；注入 Run insert 后/签名后/commit 前/commit 后/after-commit 入队失败，检查 Run数、ledger数、结果和 enqueue次数；锁顺序与跨 Thread竞争使用真实 PG，不用 Mock 代替唯一约束。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G05：发布纯 SELECT 回查路由与完整 OpenAPI

- **改动内容：** 注册独立 POST/GET 静态路径；GET 固定绑定+Auth snapshot filters 查询三态，可选 SELECT 原 Run status，不读 input/output。补齐 OpenAPI 的 headers、请求/响应/错误、缓存和通用 grant 说明；旧写入口收到 receipt digest header 先拒绝，batch 整批写入前拒绝。
- **代码位置：** 新增 `libs/langhost/src/langhost/run_acceptance.py` → `create_acceptance()` / `get_acceptance()` / 响应序列化；`libs/langhost/src/langhost/server.py` → `create_app()` / `_openapi_document()`；`libs/langhost/src/langhost/core_api.py` → `runs_create()` / `runs_batch()`；`libs/langhost/src/langhost/streaming.py::runs_stream()`、`libs/langhost/src/langhost/protocol_api.py` 的共享创建调用入口；新增 `libs/langhost/tests/test_run_acceptance_http.py`。
- **预期结果：** 回查不依赖 Thread/assistant仍存在；被 filter 排除 404、无记录/到期 unknown、错固定目标拒绝；完整响应无业务配置/私有字段；新路由先于动态 run_id 路由，普通 SDK 返回形状不变。
- **验证项：** C01–C02、C07、C12–C18、C22、C29–C31，Q01/Q02；记录 SQL mutation计数及查询前后 Run/receipt/lease/queue/outbox/hash/TTL，GET 与 POST重放均无额外写入或执行动作。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G06：长期排队保留与永久墓碑维护

- **改动内容：** accepted pending/running不按接受时间过期；维护首次观察终态/Run删除后开始默认 604800 秒详情期，rejected从记录时开始；逻辑到期立即 unknown、POST封锁，维护压缩为墓碑。复用 reaper，数据库时间、有界批次/索引、短锁复核，查询不延长任何期限。
- **代码位置：** 新增 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_acceptance.py` → `RunAcceptanceRepository.maintain_acceptance_receipts()` / 逻辑过期判定；`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` → `ProductionWorker._reaper_loop()`；新增 `libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；复用 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_state.py::is_terminal()`。
- **预期结果：** Worker=0/长排队/长运行可持续找回；终态/删除首次观测后7天压缩详情但原 key永久封住；10万行测试中每轮至多1000个实际候选，活跃老行不饿死后续终态行，维护失败不阻断 lease收割。
- **验证项：** C19–C20、C23–C24，Q01；使用 fixture数据库时间/到期数据避免等待7天，覆盖活跃/重排与清理并发、Worker重启、双维护者、Run/Thread prune、重复GET前后TTL不变和容量执行计划。
- **状态：** `[x]` 已完成 2026-10-10；实现与针对性证据见 [implementation/01-acceptance.md](implementation/01-acceptance.md)。

- **实际 Phase 验证：** `scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py`；新增 HTTP/真实 PG 测试首轮 31 passed（仅迁移种子缺 name 失败）；修正后迁移单独 1 passed；另有相关回归 47 passed/3 skipped。 这些记录不替代 Final。
- **合规检查：**
  - [x] 代码实现完成，双包 Ruff check/format 与 mypy 44 sources 通过
  - [x] 针对性 Phase 真实执行，失败与复验均保留
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 已同步实施状态

### Task G07：既有接口及双包契约回归

- **改动内容：** 复用现有官方SDK/协议/取消/webhook/Cron测试，补新路径不会改普通Run返回、Auth和幂等namespace的断言；不机械复制既有测试。校对 FEATURES/公开标准草案和使用说明，未实现阶段不标Supported。
- **代码位置：** `libs/langgraph-runtime-pg/tests/` 下 `test_official_sdk_contract.py`、`test_rest_contract.py`、`test_run_submission_concurrency.py`、`test_thread_cancellation.py`、`test_worker_cancel_propagation.py`、`test_webhook_sdk.py`、`test_cron_dispatch_live.py`；`libs/langhost/tests/` 下 `test_application_authorization.py` / `test_thread_metadata_cas.py`；`docs/FEATURES.md`、`docs/standards/rest-sse-contract.md`、`docs/compatibility/profile.md`（实现后按实际契约同步，不声称官方receipt能力）。
- **预期结果：** foreground、stream/wait、resume/HITL、Cron、取消、webhook、CAS与普通匿名开发无本项目引入的回归；官方SDK未新增不支持的参数，extension由明确HTTP adapter使用。
- **验证项：** C29–C31，Q02/Q03/Q04；复用既有两版SDK契约门禁，失败对照当前dirty基线，不把别的专项历史通过算成本项验证。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** 本轮初始相关链路最小回归：47 passed/3 skipped，55.33s，见 implementation/01-acceptance.md。完整 Final 和两版 SDK 独立记入 verification，不复用为 Phase。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

## U：使用方协作交付

### Task U01：平台保存最终传输事实并接入字节 adapter

- **改动内容：** 新增RunRequests最终key/body/digest/稳定binding与非secret Auth快照nullable字段及additive迁移；在发送前持久化，使用final bytes提交新POST。新增ports/adapter方法，保留业务request_digest，旧unknown不重算发送体、不切namespace重投；替换新路径“All 4xx=rejected”的分类，仅持久否定回执允许rejected。
- **代码位置：** `P/apps/platform-api/src/platform_api/modules/runtime_gateway/application/service.py` → `RuntimeGatewayService.launch_runtime_run()`；同模块的 `infra/sqlalchemy/models.py::RunRequestRecord`、`infra/sqlalchemy/repository.py::StoredRunRequest` / `RunRequestsRepository.create()` / `mark()`、`application/ports.py::RuntimeGatewayUpstreamProtocol`；`P/apps/platform-api/src/platform_api/adapters/langgraph/` 下 `runtime_gateway_upstream.py::LangGraphRuntimeGatewayUpstream`、`runs_sdk_adapter.py::LangGraphRunsSdkAdapter`、`runtime_client.py::LangGraphRuntimeClient` → 新增两个acceptance方法和byte transport；`P/apps/platform-api/migrations/versions/` 新增revision（由使用方核对head）。
- **预期结果：** 原 `background:<event_id>` 与最终 `platform:<hash>`不混用；默认配置/模型引用变化、进程重启或token刷新不改变已保存传输事实；网络发出前数据库已有完整记录；只有新POST创建受管后台Run。
- **验证项：** C25、C28、C31；在使用方测试环境运行 `test_run_requests.py`、`test_runtime_gateway_sdk_adapters.py` 与 `test_background_tasks.py` 对应新增case，截获transport确认bytes/header/摘要一致，注入发送前后crash，已有run_id冲突不可覆盖。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** `P/apps/platform-api/.venv/bin/python -m pytest -q P/apps/platform-api/tests/test_run_acceptance.py`：32 passed，16.62s，证据 `implementation/acceptance-platform-phase-r5.xml`；最终字节在发送前落盘、普通4xx仍unknown、旧记录不重投。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

### Task U02：固定回执委托与撤权只读通道

- **改动内容：** issuer/verifier支持专用 `run-acceptance-read` 及已签名run_acceptance claim；认证回调返回通用固定grant。核对原event/source/scope/actor/credential和最终Thread/key/digest；从可信持久快照签短TTL内部token，不重建当前模型/写授权；浏览器继续当前ACL，不能扩权为Run/state读取、执行或取消。
- **代码位置：** `P/apps/platform-api/src/platform_api/core/security/tokens.py` → `create_runtime_delegation_token()`；`P/apps/runtime-service/src/runtime_service/runtime/auth.py` → `VerifiedDelegation` / `_parse_scope()` / `verify_delegation_claims()`；`P/apps/runtime-service/src/runtime_service/auth/platform.py` → `authenticate()` / `deny_image_scope_on_server_resources()`；`P/apps/runtime-service/src/runtime_service/background_tasks/authorization.py` → `authorize_completion_cleanup()` / `is_completion_cleanup()`。
- **预期结果：** 无run_id且原操作者撤权时仍可固定GET回执；错tenant/project/Thread/key/hash/credential/event/source被拒，当前公开读仍拒绝；签发新只读token不重建模型执行能力，原过期/撤销JWT仍不可认证。
- **验证项：** C12–C16、C27；使用方 `test_runtime_delegation.py` / `test_runtime_delegation_contract.py`、Runtime `tests/runtime/test_auth.py` / `test_platform_auth.py` 和真实HTTP拒绝矩阵。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** Runtime 鉴权/执行 guard 针对性阶段：36 passed，34.26s，见 implementation/01-acceptance.md；覆盖原凭据、固定grant/read scope、过期JWT与当前公开ACL；完整115条Final独立记录。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

### Task U03：reconcile-only 与 Stop 绑定原 Run

- **改动内容：** `reconcile_only`直接GET并校验回执，不调用launch/model引用/POST；accepted回填原RunRequests，unknown保持inflight；持久拒绝不自动重投。Stop后回填run_id仍suppressed，通过既有精确取消确认，不恢复通知；保留执行前guard和旧ACL内分页正向降级。
- **代码位置：** `P/apps/platform-api/src/platform_api/modules/runtime_gateway/application/background_completion.py` → `deliver()` / `save_receipt()`；`P/apps/runtime-service/src/runtime_service/background_tasks/delivery.py` → `deliver_completion()`；`P/apps/runtime-service/src/runtime_service/background_tasks/repository.py` → `finish_delivery()` / `confirm_delivery_cancel()`；`P/apps/runtime-service/src/runtime_service/runtime/background_completion.py` → `background_completion_execution()`。
- **预期结果：** queued lost-ACK/开始前撤权找到同一Run；Stop摘要只在原取消证据成立后从unconfirmed到confirmed；unknown和过期不自动失败重试、换event/key或恢复审批；无新模型/MCP/Workspace调用。
- **验证项：** C25–C28、C32；平台 `tests/test_background_tasks.py` 与 Runtime `tests/e2e/test_background_tasks.py` 新增真实故障矩阵，捕获所有HTTP方法、执行/消息计数及持久状态；撤权后只GET，精确取消使用另一固定权限链路。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** `P/apps/runtime-service/.venv/bin/python -m pytest -q P/apps/runtime-service/tests/background/test_service.py -k "reconcile or uncertain"`：4 passed/30 deselected，23.48s，证据 `implementation/acceptance-service-phase-r5.xml`；Stop/不同run_id PG用例阶段单独1 passed，181.97s。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

## V：验收与交接

### Task V01：API/Worker 故障注入与持久性矩阵

- **改动内容：** 增加最小独立process harness，复用既有free_port/graph/进程管理，覆盖两API、Worker=0、ACK丢失、restart、迟到请求、删除/expiry、只读零副作用、key并发与Stop。只用确定性graph计数，不加真实模型依赖。
- **代码位置：** 新增 `scripts/verify_run_acceptance.py` → `main()` / 场景runner；新增 `libs/langgraph-runtime-pg/tests/test_run_acceptance.py` 与 `libs/langhost/tests/test_run_acceptance_http.py`；复用 `scripts/verify_worker_cancel_protocol.py` 和 `run_isolated_tests.py` 的既有隔离模式，不修改取消专项脚本契约。
- **预期结果：** 故障窗口只存在一个原Run；GET/重放没有重复调度；每个结果都有实际输入、进程PID/版本、Run与回执计数和结论，unknown不伪装完成；测试运行只使用可丢弃资源。
- **验证项：** C03–C11、C17–C28；Q05，输出脱敏JSON evidence，检查故障挂点确实触发；API/Worker重启不冒充数据库断电耐久测试。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** source `scripts/verify_run_acceptance.py --candidate <源码graphharbor> --output <source-process.json>`：5组实际API/Worker进程通过；证据 `implementation/source-process.json`。候选Final及三条平台联合Final独立记录。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

### Task V02：双包候选冷安装、混合版本与回退

- **改动内容：** 构建唯一隔离目录的双包wheel/sdist四产物，hash/来源/CLI/migration核验；harness使用独立site-packages与API/Worker。覆盖旧API不执行新POST、新API+旧Worker、匹配双包回退后恢复查原key；正式上传已获用户明确授权，Final门禁后执行；现役未升级。
- **代码位置：** `libs/langhost/pyproject.toml`、`libs/langgraph-runtime-pg/pyproject.toml`、`uv.lock`、`scripts/check_versions.py` / `check_compatibility_baseline.py`（版本准备时锁步更新）；新增 `scripts/verify_run_acceptance.py` → --candidate/--legacy实际参数；参考 `scripts/verify_webhook_release.py` 的冷安装来源核验。
- **预期结果：** 四产物同版本、来源和hash明确；新路径冷安装可用且无本地editable串入；旧API404/405零Run，回退不删ledger/墓碑、不重新提交原事件，恢复后查同run_id或过期unknown。
- **验证项：** C21、C30–C31；Q04/Q06；候选wheel与sdist各独立安装，固定旧基线post44，保存正式SDK兼容、API/Worker PID、迁移head与回退后记录计数。
- **状态：** `[x]` 已完成 2026-10-10。

- **实际 Phase 验证：** 双包四产物构建、site-packages冷安装及wheel阶段harness：5组通过，证据 `implementation/wheel-process.json`；新候选完整7+5组、两版SDK及正式源检查独立写Final。
- **合规检查：**
  - [x] 实现与格式检查完成
  - [x] 针对性 Phase 真实执行并记录
  - [x] tasks 状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG 同步实施/待正式发布状态

### Task V03：联合 Final、正式依赖交接与状态同步

- **改动内容：** 按verify-change执行独立Final，发布获明确指令并完成后从正式PyPI重新安装、记录来源/hashes、使用方锁接入，再跑联合矩阵。更新本项目四处状态、公开契约/CHANGELOG和来源B01；提炼经验提案，不自主写AGENTS/skills。
- **代码位置：** 本项目 `verification.md` / `tasks.md` / `README.md`、`docs/CONTEXT.md` / `FEATURES.md` / `CHANGELOG.md` / `standards/rest-sse-contract.md`；`P/uv.lock` 与 `P/docs/projects/20261009-agent-generic-production-capabilities/engine-handoff.md`（由使用方专项记录正式接入）；新增harness与平台/Runtime上述测试。
- **预期结果：** 只在32场景对应门禁及正式源/使用方锁接入均有真实证据后done/B01解除；缺正式包、使用方环境、联合Final或必要资源，按事实标partial/blocked，不用历史专项通过替代。
- **验证项：** C01–C32，Q01–Q06与使用方真实环境；Final注明实际命令、时间、版本、artifact和每场景结果，Phase/Final独立，CONTEXT/FEATURES/tasks/verification一致。
- **状态：** `[x]` 已完成 2026-10-10；正式双包 `0.13.0.post45` 发布、使用方服务锁/部署断言及发布后联合验收已完成，B01 解除；见 [implementation/02-release-closeout.md](implementation/02-release-closeout.md)。

- **实际 Phase 验证：** 收尾版本锁步、两仓库 `uv lock --check`、compatibility baseline、Ruff check/format 均 exit 0（88 files）；四正式下载产物 SHA256 与唯一发布集匹配。文档/完成卡/13条Phase/四处状态核查见 `implementation/final-closeout.json`；完整源码/发行包/联合 Final 单独记录在 verification.md，不混作阶段测试。
- **合规检查：**
  - [x] 已规划代码与正式依赖接线完成，Ruff check/format 通过
  - [x] 收尾 Phase 真实执行，Final 证据独立保留
  - [x] tasks 13/13 完成卡与状态已更新
  - [x] CONTEXT、FEATURES、CHANGELOG、公开契约及使用方交接已同步
  - [x] 标准已为 active，last_verified 为 2026-10-10；经验仅提案，未写入 lessons
