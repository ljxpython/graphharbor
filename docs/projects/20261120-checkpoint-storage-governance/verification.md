# Checkpoint 存储增长治理 - 验证计划与记录

项目启动日期：2026-11-20。通用存储的原隔离验收与正式源码交付已有证据；追加远端补验两项失败待修，当前为部分完成（partial）。进度与验收范围以[tasks.md](tasks.md)为准，原安全治理测试不计为本项目通过。


## 阶段验证（Phase）：规划与机制核查

本节为启动时的历史规划检查：当时尚未实施新日志/GC/迁移或发布。当前实施和正式交付结论见后续Phase与Final。

- 已读取本仓 CONTEXT、持久化经验、服务/发布规范和原安全治理/生命周期记录，明确 GraphHarbor 通用边界。
- 已核对 `capture_baseline()`、claim、投影与 `prune_checkpoints()`：每 Run 复制完整历史，prune 未执行 blob DELETE。前轮无数据库桩观察到复制条目随历史增加；不能替代真实 PG 体积、RSS 或线上 OOM 归因。
- 方案、S01–S07 和验收已迁入独立项目，两仓各采用 README/plan/tasks/verification 标准结构；原项目本轮新增的两份详细方案已迁出，9 份原有历史文档与各仓原提交逐字节一致。
- 两仓 `git diff --check` 与平台全仓 `python scripts/check_docs.py` 通过；本专项 14 份当前文档/导航和 69 个新增本地链接与锚点检查通过，未发现旧方案路径、旧任务编号、新增本地绝对路径或非法勾选。平台检查器不覆盖本仓全量历史文档，不据此声称其全部无缺陷。未读取、输出或复制发布凭据。
- 原官方固定版本完整差分未完成仍保留事实；用户不要求旧实现兼容，新验收检查已声明 API、安全与数据正确性。

2026-11-21 补充使用方流程边界：平台在其独立 Worktree 开发，全部功能与必要验收后迁回本地 main，由其 P02/P07 记录，不新增 GraphHarbor Git 授权。本次平台文档检查与两仓空白检查通过，14 份当前文档/导航的 71 个本地链接和锚点可达；尚未执行环境准备、产品开发或 Git 交付。

## 阶段验证（Phase）：2026-11-21源码、容量、恢复与候选

最小边界、首次before-image日志、同事务反向JSON Patch、引用安全prune/blob GC和migration015已实现。源码扩大回归399 passed/22 skipped、独立Cron3 passed；补启动Schema校验后关键矩阵24 passed。最后修复快速claim暴露的SSE首帧attempt竞态，11项stream/SDK通过；未全仓格式化既有文档代码块。

独立Python3.11 wheel环境54 passed/1 deselected；CLI竞态修复和精确CLI依赖后关键37项通过。独立Python3.13从sdist构建安装26项通过，最终stream/SDK11项通过。所有导入均来自各自site-packages，不是源码editable；生产安装无langgraph-api。原内部兼容ops用例需要可选比较依赖，初次候选该项失败后保留在源码套件，未向生产增加旧兼容依赖。

六组容量均满足用户批准门槛：边界最大330字节、WAL最大1144字节、p95最大20.214ms、RSS最大99983360字节，10000条prune最大708.201ms。旧版10000×16KiB在JSONB超过256MiB时真实失败。独立维护进程RSS不代表任意业务Worker的完整峰值；扫描/删除总工作仍随待处理行数增长，Run边界新增开销不随旧历史负载增长。

两迁移场景覆盖故障事务回退014、修复后前进、真实pg_dump/pg_restore和再次转换/逆序恢复。真实PG覆盖Delta/update_state/fork、跨namespace、HITL、覆盖写、后继拒绝、进程重启、晚写/旧generation、授权范围及GC故障原子性。独立候选HTTP/Python/JS取消矩阵24场景通过。

实际官方0.13.0双服务严格比较仍失败；201项OpenAPI描述差异与旧正式post48逐项相同，retention扩展和thread stream额外checkpoint/debug也在旧包复现。SSE首帧新增差异已修复；不宣称严格官方等价，历史比较元数据与新锁范围明确分开，未新增排除项。用户不要求旧实现逐字段兼容，本轮仍验收数据完整和安全语义。

完整输入、原始容量JSON、失败及恢复说明见[实施实证](implementation/01-storage-and-recovery.md)。构建前备份与旧正式post48的60个Python源码逐字节一致；最终轮子源码与当前源码逐字节一致，包含本轮streaming.py修复；没有.env、平台源码或Workspace。正式发布与冷安装结果见Final。

## 新实现最低验证矩阵

| 类别 | 场景与预期结果 | 最低证据 |
|---|---|---|
| 基线与日志 | 不读取复制无关历史，新增/覆盖键有精确归属和 before-image | 真实 saver/SQL、行对照与固定输入字节 |
| 回滚/投影 | A→B→回滚 B→C、逆序回滚；失败不删 Run/丢意图 | 数据逐项对照、恢复后继续执行 |
| Delta/namespace | 快照前后、update_state/fork、主子图、并行任务正确重建 | 单一依赖组合的真实 PG 通用图 |
| HITL/pending | 审批、中断任务和 pending writes 完整，恢复成功 | 公开接口与重启后的状态/运行结果 |
| GC | 仅删除无引用 checkpoint/write/blob，有效撤销和 Delta 祖先完整 | 删除前后引用集合与状态重建 |
| 有界处理 | claim/维护无全历史 Python 集合，多批每批后可恢复 | 查询/执行计划、客户端/Worker RSS |
| 并发/故障 | 晚写、旧 generation、重启/崩溃、GC 竞争与事务故障安全 | 确定性同步屏障、故障注入和重试 |
| 授权/边界 | 跨范围资源不变，核心不导入使用方业务 | HTTP/CLI 负例、代码/打包依赖审查 |
| 单格式切换 | 一次性转换可核对，失败可恢复，混跑旧 worker 拒绝 | 所属隔离副本、备份及前进恢复演练 |
| 容量 | 固定负载下逻辑字节、WAL、延迟、锁、RSS 达冻结预算 | S02 原始基线、测量命令/产物和失败记录 |
| 正式产物 | 双包唯一版本、四产物一致，正式源冷安装可用 | 哈希、导入路径、迁移 head、关键 PG 场景 |

## 阶段验证（Phase）：追加远端源码交付

2026-11-21用户另行批准经验沉淀和两仓提交推送。远端原为f9bb32e/post39，本次以eeb204c完整提交正式post49与必要的Cron、取消、webhook、回执、TTL等前置实现，以及迁移011–015、配套测试、依赖和原项目证据；origin/main已接受推送。共209个文件，没有私有配置、安装目录或已匹配凭据模式。六份历史Markdown的本机路径改为相对引用；一份历史失败XML只去掉行尾空白，保留失败内容。

重新下载PyPI四产物，哈希均与原发布记录一致；runtime51个、CLI11个Python源码逐字节一致。初次Python默认CA验证失败，改用已安装certifi可信CA后成功，未跳过TLS验证。uv lock --check、版本锁步、兼容基线、Ruff/90文件格式、mypy46源码及diff --check通过；mypy仅保留未用deepagents配置提示。既有功能测试因源码未变化而复用，不记成重新执行。平台三条经验主题维护在其Runtime经验库，未引入本仓核心。

没有重复发包、创建tag或部署现役。[远端CI44787814603](https://github.com/ljxpython/graphharbor/actions/runs/44787814603)实际针对eeb204c执行：Build、Lint及Python3.11/3.12/3.13生产契约共五项通过；Test和JavaScript SDK contract两项失败。

Test失败步骤为First-party live-server E2E，13例中10 failed/3 passed，另有20 rerun。该夹具通过langgraph_api.server:app及LANGGRAPH_RUNTIME_EDITION=pg运行内部适配入口；日志显示graphharbor_journal_checkpoint_blobs写run_checkpoint_undo时，run_id缺少run_checkpoint_baselines父行，触发run_checkpoint_undo_run_id_fkey。writer/run baseline链路需要修复复验，原正式源未纳入可选ops不能证明此入口通过。

JavaScript SDK1.9.28的完整契约返回422 Invalid thread metadata update。错误对应core_api.py的线程metadata更新校验；具体触发请求及成因尚未完全确认，不能把候选原因写成结论。旧f9bb32e的Test/JS任务曾通过，本次失败不能按既有失败直接排除。两项均未修复，已写回S07；当前项目不是全项完成或生产就绪，原冷安装和隔离容量结果保留其各自范围。

## 整体验收（Final）：2026-11-21原隔离与正式包验收记录

按用户批准的反向差量与绝对预算核对S01–S07。复用最终候选同源码的六组容量、确定性并发/故障和转换备份恢复证据；正式源另外执行42项真实PG/迁移/Schema启动门禁/SDK/CLI/stream测试，42 passed/1 deselected。正式源最初因测试专用asgi-lifespan缺失而collection失败；补齐后内部兼容ops一项因可选langgraph-api缺失失败，最终明确不纳入该内部入口，保留源码套件覆盖，不给生产添加兼容依赖。数量重叠不相加，未计skip为通过。

双包0.13.0.post49四产物正式发布，PyPI公布hash与已审核文件相同。Python3.13全新venv通过PyPI无缓存安装，CLI输出post49，两包实际导入site-packages，迁移head015。包源码逐字节匹配当前工作树；相对旧正式版仅8个runtime、2个CLI Python文件新增/变化，具体清单见[发布证据](implementation/release-post49.json)。未引入平台业务依赖。

正式冷安装环境另运行HTTP/Python/JS取消24场景，结果为`{"graphharbor":24}`，公共终态/取消传播保持。

六组Run边界≤330B、WAL≤1144B、边界p95≤20.214ms、独立维护RSS≤99,983,360B，10,000条prune≤708.201ms，均低于已批8KiB/64KiB/100ms/256MiB/5s。prune扫描成本仍随历史行数增长；本验收只对约定夹具负责。

严格官方比较仍有既有差异，不能标为严格等价通过；原官方等价性专项继续保留自己的未完成范围。用户已要求单一新实现而非旧代码兼容，本项目没有扩大排除项。生产负载、任意业务Reducer的大状态和生产升级未验也未执行。GraphHarbor Git交付已按追加授权完成；使用方业务联合验收在其项目独立记录。

上述为远端补验前的原隔离与正式包验收事实。后续两项远端失败尚未修复，当前项目重新标为部分完成，不以原Final覆盖新失败。正式发包、反向差量投影、验收预算及后续源码提交推送均已有用户明确批准；不把本地验证当作独立人审或生产验收。GraphHarbor仓库没有新增现役库迁移、业务数据销毁或生产服务切换授权。
