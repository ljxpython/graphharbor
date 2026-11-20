# Run 接受回执验证设计与记录

> 状态：`done`，13/13任务完成。源码/候选、正式PyPI wheel/sdist 冷安装与真实进程、使用方正式锁接入及发布后 notify/Stop/撤权联合Final均通过；B01解除，现役未部署。Phase与Final证据分开记录。

## 1. 基线与隔离要求

- 规划基线：源码/正式双包 `0.13.0.post44`，migration `012_run_callback_deliveries`。实施前重新记录版本、schema head、工作树来源和其他并行专项变更；不覆盖已有dirty工作树。
- 复用 `scripts/run_isolated_tests.py`，临时initdb/PostgreSQL、随机端口Redis、独立数据库与key前缀，不读仓库.env或生产凭据。runtime fixture会truncate，必须由runner注入隔离URI后才能运行。
- `scripts/test.sh` 含破坏性清库，不得直接运行；如最终规范要求使用，只能置于确认可丢弃的隔离环境。
- 既有runner默认PG使用 `fsync=off`，可验证数据库事务、API/Worker进程重启和并发，**不能据此声称数据库断电耐久或跨备份回滚防重**。本项目不以未执行的PG断电测试作保证。
- 不需要真实模型。用确定性graph和计数器模拟执行、消息、模型/MCP/Workspace调用；GET本身必须全部零动作。正常故障重启/at-least-once执行的既有保证照旧，不承诺所有外部副作用exactly-once。
- 已实施新测试与harness。命令与实际结果见下方Final；私有env/ready文件不收录为证据。

## 2. 场景矩阵（32项）

每项记录请求/响应、固定Thread/key_hash/digest、相关Run与ledger行数、enqueue计数和副作用计数；安全场景不得输出bearer token、input/config、授权metadata或用户原业务ID。

| ID | 故障或边界 | 可判定结果 | 责任任务 |
|---|---|---|---|
| C01 | 独立POST/GET、静态路由顺序与OpenAPI | 正确路由/headers/schema/cache；旧SDK普通Run响应不变；新摘要header放错入口零副作用422 | G01/G05 |
| C02 | raw字节摘要、重复key/header、非法JSON/NaN、bodykey/私有上下文 | 固定向量一致；字节变化摘要变化；非法请求零Run/ledger/enqueue | G01/G05 |
| C03 | 网络请求尚未到达或事务尚未开始 | GET=unknown；不持久化假拒绝、不自动POST | G04/V01 |
| C04 | Run插入后签名/flush异常或commit前rollback | Run与receipt均未提交；GET=unknown；无after-commit enqueue | G04/V01 |
| C05 | commit成功后丢掉HTTP ACK | GET=accepted且原run_id；一份Run/receipt；没有二次提交 | G04/V01 |
| C06 | same key/same bytes重复POST | 同run_id/accepted_at；kwargs/签名/TTL不变；只首次注册enqueue | G04/V01 |
| C07 | same key换body、字节格式或Thread | 409冲突；原Run/receipt不变；不泄露旧目标详情，固定grant篡改先拒绝 | G01/G04/G05 |
| C08 | 两API实例同key同Thread并发 | 真实PG唯一槽位；同run_id；一份pending Run；无deadlock遗漏 | G04/V01 |
| C09 | 两API同key跨Thread/不同body并发 | 一个胜出，另一个409；advisory hash碰撞只串行不混淆身份 | G04/V01 |
| C10 | 未提交事务期间GET，随后原POST迟到提交 | 先unknown后accepted；首次unknown不能推出未接受或触发重发 | G04/V01 |
| C11 | 忙Thread+reject持久拒绝，Thread随后空闲且旧请求迟到 | definitively_not_accepted；该key永久封锁；零该key Run；普通4xx/5xx仍不是否定证据 | G04/V01 |
| C12 | 有/无Auth、匿名、缺grant、无handler、Auth返回false/filter | 新路径明确拒绝缺必要条件，合法handler遵循SDK分派；旧匿名行为不变 | G02/G05 |
| C13 | 同subject跨opaque scope/credential及token刷新jti | 跨binding不能读原回执；jti变化不破坏原稳定binding；客户端自称binding不生效 | G02/U02 |
| C14 | 篡改Thread/key/hash/digest、重复headers、伪造receipt marker | 与可信固定target不符拒绝；无数据暴露或执行权限 | G02/G05/U02 |
| C15 | callback filter与历史Thread metadata快照不符 | SQL过滤后404；不忽略filter、不用in_principal_scope假装隔离 | G02/G05/U02 |
| C16 | read委托访问普通Run/state/store/日志、POST或取消；公开用户撤权 | 全部拒绝；固定回执例外只GET，当前公开ACL照旧 | G02/G05/U02 |
| C17 | accepted之后显式删Run | GET仍accepted/原run_id，run_state.available=false；同keyPOST不重建 | G03/G05/V01 |
| C18 | accepted之后Thread delete/prune及Run rollback删除 | ledger不级联消失，合法内部固定回执仍可读；授权快照不公开 | G03/G05/V01 |
| C19 | pending/running超过7天、Worker=0、长排队/运行 | 完整accepted不因接受时间过期；GET不写TTL；正常requeue/drain不误清活跃Run | G06/V01 |
| C20 | 终态/删除观察后详情到期，清理尚未执行或墓碑已存在 | 两种GET均unknown/expired，同keyPOST409；原key永久不可重新执行 | G03/G06/V01 |
| C21 | 空库/012升级、禁止downgrade、旧Run无回执、匹配包回退 | 单一head、旧数据不回填猜测；无证据unknown，ledger/墓碑保留；旧接口可用 | G03/V02 |
| C22 | 连续GET与POST回放，含accepted/rejected/unknown/expired | 零业务写SQL；Run/lease/队列/outbox、输入、期限不变；模型/MCP/Workspace为0 | G05/V01 |
| C23 | 10万行、活跃老行+后续终态、双维护者、清理/重排竞争 | 每轮最多1000个候选，索引可用、无饥饿；SKIP LOCKED/短锁；不清活跃详情、不阻断reaper | G06 |
| C24 | after-commit Redis失败、API和Worker停止/重启 | 已提交accepted可查，PG pending可恢复；不是“入队成功才接受”；维护停止只延后压缩 | G04/G06/V01 |
| C25 | 平台发送前或ACK落盘前crash、默认配置/模型引用变化 | final key/body/digest/binding发送前持久；恢复GET原key；仅一个原Run，无重算重发 | U01/U03/V01 |
| C26 | Worker=0时queued lost-ACK后Stop | 内部GET找回原run_id，保持suppressed并精确取消；真实停止证据后才confirmed，无通知复活 | U03/V01 |
| C27 | 原actor执行前撤权，固定只读内部清理 | 新只读委托可GET原回执，公开读/新执行拒绝，execution guard保留；错tenant/project/credential等签发拒绝 | U02/U03/V01 |
| C28 | reconcile_only=unknown/expired/404/超时/4xx/持久拒绝；旧记录无摘要 | 只有GET或当前ACL内有界分页list正向找回，无launch/model引用/POST、换event/key或自动通知重投；仅持久否定回执可标rejected | U01/U03/V01 |
| C29 | foreground、stream/wait、Cron、HITL/resume、CAS、取消/webhook回归 | 既有公开HTTP/SSE/SDK行为无本项回归，普通namespace保持原语义 | G07 |
| C30 | 双包wheel/sdist冷安装及两版SDK/新API+Worker进程 | 同版本四hash、site-packages来源、CLI与新接口、schema head真实核验 | G07/V02 |
| C31 | 新POST命中旧API、新API+旧Worker、两个新API、回退后恢复 | 旧API404/405零执行，绝不退旧POST；回退保留ledger，新版恢复读同key；不混namespace投同事件 | U01/G07/V02 |
| C32 | 正式PyPI安装、使用方锁接入与联合Final | 真实两进程完整矩阵、最多一个接受Run/业务通知，无回查副作用；B01仅在正式证据齐全后解除 | U03/V03 |

## 3. 验证命令

所有命令从 GraphHarbor 根目录执行。文档省略终端代理前缀；真实执行使用rtk。使用方在自身Worktree环境运行，不把GraphHarbor URI接入现役库。Final结果只认下方实际产物。

### Q01：针对性新逻辑与真实PG事务

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py libs/langgraph-runtime-pg/tests/test_run_acceptance.py libs/langhost/tests/test_application_authorization.py libs/langgraph-runtime-pg/tests/test_run_submission_concurrency.py libs/langgraph-runtime-pg/tests/test_schema_setup.py
```

新增文件须覆盖G01–G06对应场景，不只镜像内部实现；SQL唯一/rollback/锁/过滤/过期使用真实PG。

### Q02：既有相关链路回归

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python -m pytest -q libs/langgraph-runtime-pg/tests/test_official_sdk_contract.py libs/langgraph-runtime-pg/tests/test_rest_contract.py libs/langgraph-runtime-pg/tests/test_thread_cancellation.py libs/langgraph-runtime-pg/tests/test_worker_cancel_propagation.py libs/langgraph-runtime-pg/tests/test_webhook_persistence.py libs/langgraph-runtime-pg/tests/test_webhook_sdk.py libs/langhost/tests/test_thread_metadata_cas.py
```

### Q03：Cron及Final全量

```bash
.venv/bin/python scripts/run_isolated_tests.py --database-name graphharbor_cron_probe_20261005 .venv/bin/python -m pytest -q libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python -m pytest -q -m "not e2e" --ignore=libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py
```

`e2e`需要真实服务地址，不用`not e2e`结果冒充完整E2E；Q05/Q06和使用方联合环境承担本项两进程验收。全量只在Final按规范执行，开发阶段不无原因反复重跑。

### Q04：静态、锁步与候选四产物

```bash
uv run ruff check libs/langhost libs/langgraph-runtime-pg
uv run ruff format --check libs/langhost libs/langgraph-runtime-pg
uv run mypy
uv lock --check
python3 scripts/check_versions.py
python3 scripts/check_compatibility_baseline.py
```

构建输出必须选择全新临时目录，保留原有产物，不清dist；示例脚本只建候选，不上传：

```bash
python3 - <<'PY'
import subprocess
import tempfile
from pathlib import Path

acceptance_artifacts = Path(tempfile.mkdtemp(prefix="graphharbor-acceptance-artifacts-"))
for package in ("graphharbor-runtime", "graphharbor"):
    subprocess.run(["uv", "build", "--package", package, "--out-dir", str(acceptance_artifacts)], check=True)
print(acceptance_artifacts)
PY
```

### Q05：新增进程故障harness

实际harness采用一套场景与显式安装入口，独立runner负责PG/Redis：

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_run_acceptance.py --candidate <GraphHarbor源码venv绝对路径>/bin/graphharbor --output docs/projects/20261010-run-acceptance-receipts/implementation/source-process.json
```

harness在自己的临时工作目录启动两API与可控Worker，所以candidate/legacy/graphharbor可执行文件必须给绝对路径。C03–C11等事务细粒度用真实PG单测补齐，C25–C28由使用方联合运行验证。

### Q06：冷安装与混合/回退harness

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_run_acceptance.py --candidate ".release-candidates/run-acceptance-post45-r2/wheel/bin/graphharbor" --legacy ".release-candidates/run-acceptance-post45-r2/legacy/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-wheel-mixed.json
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_run_acceptance.py --candidate ".release-candidates/run-acceptance-post45-r2/sdist/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-sdist.json
```

四产物先安装到相互独立venv，再用上述同一harness，避免四个重复mode。拒绝非disposable库，清除PYTHONPATH，从site-packages启动API/Worker。混合与代码回退保留ledger，测试显式调整匹配旧代码的revision marker后恢复013；不是破坏性Alembic downgrade，013的拒绝downgrade另有真实PG断言。

## 4. 证据要求与判定

证据最少包含时间、运行命令、源码/安装版本、schema head、artifact hashes、API/Worker PID、故障挂点确实发生、回查HTTP结果、Run/ledger/队列/outbox前后计数、模型/MCP/Workspace/消息计数和场景ID。测试库允许SQL核验事实；生产使用方只能走正式adapter，不直接查引擎表。

- `done`：G/U/V任务和本矩阵适用门禁全部有证据，正式双包/使用方锁接入和联合Final完成。
- `partial`：引擎实现/候选通过但正式包、使用方或联合验收未完；明确剩余任务，不提前解除B01。
- `blocked`：必要环境、正式产物或人工执行授权确实无法取得；保留已完成证据，不回退unknown为成功。
- `not-done`：关键语义仍失败、必要实现尚缺，不能发布“可靠接受回执已完成”。

## 5. Phase 记录

### 2026-10-10：规划交付检查

- 已完成：交接文档/当前源码只读核查、HTTP/事务/Auth/保留期/切换方案和13项任务、32场景设计。
- 文档静态检查：执行 `python3` 内联assert自查，逐项核对四文件、Task五个字段、未勾选状态、连续场景编号、本地链接/既有代码路径、空白及CONTEXT规划状态。实际输出：`PASS: 4 project files, 13 unchecked four-part tasks, 32 scenarios, 11 local links, 41 existing paths; whitespace/status checks passed`。
- 空白检查实际命令：`git diff --check -- "docs/CONTEXT.md" "docs/projects/20261010-run-acceptance-receipts"`，exit 0、无输出；新增未跟踪文档的尾随空白和EOF换行由上述Python检查覆盖，未以git diff遗漏未跟踪文件冒充已检。
- 实现/故障测试：未执行；此条不是功能Phase通过，不计实现任务完成数。

### 实施 Phase 完成卡

- [x] Task G01：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G02：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G03：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G04：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G05：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G06：实际命令/结果见 tasks 完成卡与 implementation/01-acceptance.md。
- [x] Task G07：47 passed/3 skipped相关Phase，完整Final独立记录。
- [x] Task U01：32 passed，acceptance-platform-phase-r5.xml。
- [x] Task U02：36 passed鉴权/guard阶段，见实现记录。
- [x] Task U03：4 passed/30 deselected，acceptance-service-phase-r5.xml，另有Stop PG单独通过。
- [x] Task V01：source-process.json，5组真实进程Phase。
- [x] Task V02：wheel-process.json，5组冷安装Phase。
- [x] Task V03：收尾版本/锁/hash/静态与四处状态检查通过，见 tasks 完成卡、implementation/02-release-closeout.md 和 final-closeout.json；系统级 Final 独立见下文。

## 6. Final 记录（独立）

### 6.1 源码与静态门禁（2026-10-10）

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python -m pytest -q -m "not e2e" --ignore=libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py --tb=short -o faulthandler_timeout=120 --junitxml=docs/projects/20261010-run-acceptance-receipts/implementation/final-engine.xml
.venv/bin/python scripts/run_isolated_tests.py --database-name graphharbor_cron_probe_20261005 --redis-prefix graphharbor:cron-probe-20261005 .venv/bin/python -m pytest -q libs/langgraph-runtime-pg/tests/test_cron_dispatch_live.py --junitxml=docs/projects/20261010-run-acceptance-receipts/implementation/final-cron.xml
.venv/bin/python -m pytest -q libs/langhost/tests/test_run_acceptance_http.py --tb=short --junitxml=docs/projects/20261010-run-acceptance-receipts/implementation/final-http-extra.xml
```

- 全量：**371 passed、9 skipped、13 deselected，200.69s**；Cron独立：**3 passed，10.73s**。skip/deselected不能算通过；公开API/Worker由真实harness和联合场景补验。
- 最后新增固定摘要向量、重复digest header与raw digest不符断言：**23 passed，9.05s**。仅改测试，两个发布wheel SHA256未变。
- 双包/harness Ruff check、format（87 files）、mypy（44 sources）、uv lock --check、版本锁步与compatibility baseline均通过。
- 早前全量曾卡在冷Alembic导入/新进程启动。测试启动预算改90s、超时回收进程；未放宽业务取消/attempt预算。历史失败不删除、不并入通过数。

### 6.2 独立安装与真实两进程

Q06实际执行两条命令，候选wheel混合旧post44 **7组通过**、sdist **5组通过**。证据 [final-wheel-mixed.json](implementation/final-wheel-mixed.json)、[final-sdist.json](implementation/final-sdist.json) 包含时间/命令、site-packages路径、两个API/Worker PID、head、Run/ledger计数。覆盖Worker=0、两API并发、20次只读、queued Stop、持久忙碌拒绝、ACK丢失、API kill/restart、after-commit入队失败后的PG恢复、删除/expiry、旧API405零执行、新API+旧Worker及保留ledger代码回退再升级。

```bash
.venv/bin/python scripts/run_isolated_tests.py .release-candidates/run-acceptance-post45-r2/wheel/bin/python scripts/verify_worker_cancel_protocol.py --kind graphharbor --graphharbor ".release-candidates/run-acceptance-post45-r2/wheel/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-sdk-043.json
.venv/bin/python scripts/run_isolated_tests.py .release-candidates/run-acceptance-post45-r2/sdist/bin/python scripts/verify_worker_cancel_protocol.py --kind graphharbor --graphharbor ".release-candidates/run-acceptance-post45-r2/sdist/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-sdk-046.json
```

Python SDK **0.4.3和0.4.6各24组通过**，其中包含HTTP与JS SDK1.9.28。第二环境使用 `uv --no-config pip install --python <sdist-python> langgraph-sdk==0.4.6`，源码SDK锁仍0.4.3。

四产物以 [artifacts-final.json](implementation/artifacts-final.json) 为发布唯一集合；gateway sdist因补固定摘要测试重建，两wheel与通过harness的候选完全同hash。旧artifacts-r2保留为候选历史。

### 6.3 使用方源码与候选联合Final

使用方仓库根目录简称 `P=../ai-agent-platform`，实际测试使用其登记 Worktree；测试产物的脱敏副本收录implementation。

```bash
P/apps/platform-api/.venv/bin/python -m pytest -q P/apps/platform-api/tests/test_run_acceptance.py P/apps/platform-api/tests/test_run_requests.py P/apps/platform-api/tests/test_background_tasks.py P/apps/platform-api/tests/test_runtime_gateway_sdk_adapters.py P/apps/platform-api/tests/test_runtime_delegation.py P/apps/platform-api/tests/test_iam_migration.py P/apps/platform-api/tests/integration/test_run_acceptance_migration.py --tb=short --junitxml=P/.local-stack/acceptance-platform-final-r5.xml
P/apps/runtime-service/.venv/bin/python -m pytest -q P/apps/runtime-service/tests/runtime/test_auth.py P/apps/runtime-service/tests/runtime/test_platform_auth.py P/apps/runtime-service/tests/background/test_completion.py P/apps/runtime-service/tests/background/test_service.py --junitxml=P/.local-stack/acceptance-runtime-final.xml
```

- Platform：**132 passed、1 skipped、116 subtests，45.39s**；skip是未注入PG URL的postgres参数，由下面独立实测补齐。
- Runtime：**115 passed，17.16s**；uncertain状态先读固定回执，不先走当前ACL；执行guard仍当前授权。
- 全Runtime repository在disposable PG：**21 passed、5 skipped，45.33s**，证据 [acceptance-repository-final.xml](implementation/acceptance-repository-final.xml)。命令使用隔离runner再在子进程设置 `BACKGROUND_TEST_DSN=POSTGRES_URI`，未读取现役DSN。5条可选Docker用例未启用，三条联合场景另用真实Docker任务。
- 平台additive migration真实PG：**1 passed、1 deselected，8.77s**。runner子进程设置 `ACCEPTANCE_TEST_PLATFORM_DATABASE_URL` 为其disposable URL，执行 `tests/integration/test_run_acceptance_migration.py -k postgres`；0006旧行未变、6字段NULL、二次head幂等、已保存bytes/快照在拒绝downgrade后仍保留，head0007。SQLite同语义在132条中通过。
- 两仓库本轮dirty Python文件Ruff check/format通过，证据 [acceptance-static-platform.json](implementation/acceptance-static-platform.json)。未覆盖另会话无关产品变更。

真实联合case在 **各自独立runner** 执行，cwd为 `P/apps/runtime-service`：

```text
TOOL_ERROR_PLATFORM_NATIVE=1 PLATFORM_API_TEST_PYTHON=<P平台python> <GraphHarbor-python> scripts/run_isolated_tests.py <P-runtime-python> -m pytest -q "tests/e2e/test_background_tasks.py::test_lost_native_ack_reconciles_queued_run_without_resubmission[<notify|stop|revoke>-stack0]" --tb=short --basetemp=<本Worktree独立目录> --junitxml=<本Worktree证据.xml>
```

| 场景 | 候选实际结果 | 可见事实 |
|---|---|---|
| notify | 1 passed，155.08s | Worker=0时已accepted，随后GET找回；2 Run（源+完成）、1 ledger、完成模型1次 |
| stop | 1 passed，227.12s | GET绑定原Run后仍suppressed；精确取消使Stop cleanup从未确认到confirmed；完成模型0 |
| revoke | 1 passed，252.25s | 公开list/新执行403；固定GET仍accepted，执行guard最终error；完成模型0 |

证据：`candidate-joint-{notify,stop,revoke}.xml/json`。先前合跑三参数失败记录 `acceptance-joint-r2.xml` 保留：执行marker被误当完整event摘要、错误Stop测试路径、uncertain先当前ACL。已修共享根因并以独立库复验；不把失败归咎未知环境。

新增tests最初UUID DTO/SQLite反射类型和now默认值夹具失败；修正为正确UUID转换及显式fixture时间，`acceptance-platform-final-r4.xml` 与早前SQLite失败保留，最新r5通过。生产迁移只新增nullable字段。

### 6.4 场景到实际证据映射

下表按32项责任边界关联实际断言，不把一个场景名当作整套未运行保证。E=final-engine.xml，H=final-http-extra.xml，W=final-wheel-mixed.json，S=final-sdist.json，P=acceptance-platform-final-r5.xml，R=acceptance-runtime-final.xml。

| 场景 | 实际验证入口 |
|---|---|
| C01 | H/OpenAPI、旧入口422；E普通SDK协议 |
| C02 | H固定Unicode/空格/键序/null/数值/数组向量、非法JSON/headers |
| C03 | E concurrent_replay/no_receipt；W/S Worker=0 |
| C04 | E failure_after_insert/commit_failure |
| C05 | W/S lost-ack-api-kill-restart；候选joint notify |
| C06 | E concurrent_replay/reads_and_replays；W两个API |
| C07 | E字节变化/跨Thread409；H错固定目标 |
| C08 | E concurrent_replay；W/S两个API |
| C09 | E cross_thread_concurrency_and_missing_ledger |
| C10 | E uncommitted_receipt_is_unknown_then_accepted |
| C11 | E only_persistent_busy_rejection_closes_key；W/S |
| C12 | H anonymous_and_auth_without_handler；E应用授权 |
| C13 | E跨credential unknown；P稳定binding/jti；R verifier |
| C14 | H唯一header/错target；P/R grant篡改和签名 |
| C15 | E owner filter 404与历史metadata快照 |
| C16 | H read grant禁止其他资源；R当前ACL；joint revoke公开403 |
| C17 | E删除Run；W/S deletion-expiry |
| C18 | E同时删Thread/Assistant后仍回放原回执、无FK |
| C19 | E active_retention与capacity重排；W/S Worker=0 |
| C20 | E逻辑expiry/墓碑；W/S删除和expiry |
| C21 | E additive012/no FK/拒downgrade；W保留ledger代码回退；平台PG独立 |
| C22 | E reads_and_replays SQL监听/唯一enqueue、GET不改TTL；W/S计数 |
| C23 | E 10万行EXPLAIN索引、双维护各1000、活跃重排保留 |
| C24 | E签名/入队失败；W/S kill/restart及PG恢复 |
| C25 | P final_bytes_commit_before_send/no_repeat；joint lost ACK |
| C26 | joint stop、repository finish_delivery/suppressed/固定run_id |
| C27 | joint revoke；P/R固定read scope、当前guard拒执行 |
| C28 | P strict DTO/unknown/error/旧ACL分页最多10×100、event+task+source匹配；R uncertain GET |
| C29 | E全量既有Run/SSE/HITL/CAS/取消/webhook；Cron独立3；SDK各24 |
| C30 | W/S site-packages/head/PID；四hash；SDK两版 |
| C31 | W旧API405零执行、新API旧Worker、保留ledger再恢复；P旧记录不换namespace |
| C32 | pypi-formal.json / final-pypi-wheel.json / final-pypi-sdist.json；platform-formal-lock/install.json；formal-joint-notify/stop/revoke.xml/json，B01已解除 |

### 6.5 正式 PyPI 与发布后联合 Final（2026-10-10）

按 runtime → graphharbor 顺序使用本地 `UV_PUBLISH_TOKEN` 上传双包 wheel/sdist，令牌仅在上传进程环境中。记录见 [pypi-upload.json](implementation/pypi-upload.json)。随后从 PyPI JSON/文件服务独立下载四产物，SHA256 与 [唯一发布集](implementation/artifacts-final.json) 全部匹配；wheel 和 sdist 各冷安装到独立 venv，双包均 `0.13.0.post45`、SDK `0.4.3`、CLI 正确、源码来自各自 site-packages、迁移 head `013_run_acceptance_receipts`，见 [pypi-formal.json](implementation/pypi-formal.json)。

```bash
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_run_acceptance.py --candidate ".release-candidates/run-acceptance-post45-r2/pypi-wheel/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-pypi-wheel.json
.venv/bin/python scripts/run_isolated_tests.py .venv/bin/python scripts/verify_run_acceptance.py --candidate ".release-candidates/run-acceptance-post45-r2/pypi-sdist/bin/graphharbor" --output docs/projects/20261010-run-acceptance-receipts/implementation/final-pypi-sdist.json
```

正式 wheel **5组通过**，sdist **5组通过**，证据 [wheel](implementation/final-pypi-wheel.json) / [sdist](implementation/final-pypi-sdist.json)。各自 disposable PG/Redis 中覆盖两API同key并发、Worker=0及20次GET零执行、queued Stop/永久忙碌拒绝、ACK丢失/API kill/restart/Worker恢复、commit后入队失败的PG恢复、删除/expiry防重。每条记录含实际 executable、版本/来源/head、API/Worker PID、Run/ledger数量与执行次数。首轮wheel相对executable路径因harness切换临时cwd无法启动；改用绝对路径复验通过，不把启动失败计为功能通过。

使用方 `P/apps/runtime-service/pyproject.toml`、服务 `uv.lock` 和 Dockerfile 已锁定 post45；`uv lock --check` exit 0。锁文件记录正式 PyPI URL/四hash，Worktree 两包来自自己的 site-packages、无 editable/direct URL，平台新增迁移为 `20261010_0007`。见 [锁证据](implementation/platform-formal-lock.json)、[安装证据](implementation/platform-formal-install.json)。

发布后联合命令沿用6.3的同一测试入口，在 `P/apps/runtime-service`、各场景独立runner和本Worktree独立basetemp执行，JUnit产物为 `P/.local-stack/acceptance-<notify|stop|revoke>-pypi.xml`。正式依赖已从PyPI同步；未读生产DSN、未停启其他会话的本地栈。

| 场景 | 正式源实际结果 | 接受事实与副作用 |
|---|---|---|
| notify | 1 passed，249.96s | ACK丢失/Worker暂停后GET找回原Run；源+完成2 Run、1 ledger、完成模型1次 |
| stop | 1 passed，322.48s | 同原Run，suppressed保持；精确取消后cleanup confirmed，2 Run/1 ledger、完成模型0次 |
| revoke | 1 passed，339.07s | 公开list/新执行403；内部固定GET可读，execution guard拒绝模型，2 Run/1 ledger、完成模型0次 |

脱敏副本 `formal-joint-{notify,stop,revoke}.xml/json` 保留在 implementation；无 JWT、私有env或完整应用日志。正式结果覆盖C32并解除平台B01；原平台全范围 T08/T10/F12、新Linux应用镜像及现役启用不在本专项完成声明内。

### 6.6 收尾检查

双包版本锁步、两仓库 `uv lock --check`、compatibility baseline、双包/harness Ruff check/format（88 files）均 exit 0。文档本地链接、EOF/尾随空白、完成卡、13条Phase、32条场景映射、四处done状态和证据结构检查见 [final-closeout.json](implementation/final-closeout.json)。上传/安装证据已按文件名核验四hash；证据目录的JWT/PyPI token/私钥模式扫描无匹配。该扫描只用于避免证据意外带入凭据，不代表额外安全审计。

收尾检查覆盖20份文档、79个本地链接和7个锚点，13完成卡/13条Phase一致，58个Python源码文件与正式wheel逐一匹配；引擎 `git diff --check` 和使用方本轮文档范围检查通过。使用方全仓 `python3 scripts/check_docs.py` 仍有39条其他文档的本机绝对路径问题；全仓 `git diff --check` 另报告3个未由本轮修改的经验文件EOF空行。两项范围外结果记录在final-closeout.json，本专项不声称平台全仓门禁全部通过。

## 7. 四处状态与当前判定

- [x] README状态为**done、13/13**。
- [x] tasks的13项全部完成，完成卡/合规检查完整。
- [x] 实施Phase记录为13条，与完成任务数一致；Final独立保留。
- [x] CONTEXT项目行与双包状态已同步**done、正式post45、B01解除**。
- 当前四态：**done**；规划范围内引擎/使用方接线、候选与正式源Final、双包发布和正式依赖交接均完成。
- 边界：不部署现役、不执行git操作；墓碑随key线性增长；备份恢复到接受前会丢防重事实；fsync=off隔离测试不证明断电耐久。固定回执只证明接受，不证明停止/通知或任意外部副作用exactly-once。
- 标准收尾：对应REST/SSE规范已为active、last_verified=2026-10-10，健康表一致；没有待毕业草案。
- 经验提案（待用户确认，未写入lessons）：冷安装进程harness会切换临时cwd，CLI必须传绝对路径并核验site-packages/head；原接口回归、接受回执保证与使用方全栈完成须分别记录，解除B01不等于现役可启用。
