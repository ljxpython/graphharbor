# Thread 到期与可靠删除任务

进度唯一来源。每项完成补充代码位置、实测命令和结果；全部完成前不作最终交付。

- [x] G01 职责边界与人工批准：2026-10-30 用户对话确认；方案/任务/验收已记录。
- [x] G02 Schema、策略与受鉴权 retention/touch：固定/闲置语义、服务端时间、存量不误删。
- [x] G03 统一可靠删除：并发保护、引擎数据矩阵、事务清理、持久任务、前后钩子与恢复。
- [x] G04 维护 CLI/调度与观测：report 只读、有界 apply、多实例保护、阻塞和失败可查询。
- [x] G05 源码验证：针对性、回归、真实 PostgreSQL/Redis、重启/故障/并发/鉴权与迁移回退。
- [x] G06 双包构建与正式发布：锁步、lint/type/build/test；四产物 SHA256；本地令牌直传。
- [x] G07 PyPI 冷安装验证与平台交接：版本/CLI/真实链路；同步契约/能力/变更/上下文。

状态：已完成（done，7/7 任务；Phase与Final核验通过）。全部完成于 2026-10-30，细节见 [实现记录](implementation/01-thread-retention.md)；Phase 证据逐项见 [验证记录](verification.md)。

## Task Completion Cards

### G01 职责边界与人工批准

- **改动内容：** 将通用生命周期归 GraphHarbor，业务策略归使用方；明确直接发布授权。
- **代码位置：** `plan.md`、本任务文件；本项不改源码。
- **预期结果：** 核心不携带平台期限、Workspace 或业务表。
- **验证项：** 用户 2026-10-30 明确同意边界、开发/本地打包/正式发布与验证；已核对仓库规范和既有改动基线。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 方案工作完成；无代码实施项
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT 已同步；FEATURES/CHANGELOG 跳过（规划项）。

### G02 Schema、策略与鉴权接口

- **改动内容：** migration014、fixed/idle 策略、显式活动、retention/touch/deletion 接口及 OpenAPI。
- **代码位置：** `retention.py` → `validate_policy()/set_policy()/touch()`；`models.py`；`migrations/versions/014_thread_retention.py`；`langhost/core_api.py`、`server.py`。
- **预期结果：** 分钟单位、服务端时间；普通读取和自动运行不续期；存量无倒推 TTL。
- **验证项：** `test_thread_retention.py`、REST/官方 SDK 契约及正式 HTTP policy/touch/read 三项通过，见 G05/G07。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 代码实现完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT、FEATURES、CHANGELOG 和 active REST 契约已同步。

### G03 可靠删除与恢复

- **改动内容：** DELETE/prune delete/TTL 统一任务与删除屏障；前后 hook、短事务、持久 claim/重试及完成墓碑精简。
- **代码位置：** `retention.py` → `request_deletion()/_blocked()/_engine_delete()/advance_deletion()`；`checkpoint_mutations.py`、`run_store.py`、`ops.py`。
- **预期结果：** 活跃/未确认执行保护；失败可续跑；完成后 UUID 不复活，保留最小凭证。
- **验证项：** PG 删除矩阵、并发、hook 缺失/失败、lease/cron 保护与 metadata 精简通过；正式包 before/after retry、restart、fence、tombstone 通过。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 代码实现完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT、FEATURES、CHANGELOG 和表级保留说明已同步。

### G04 维护 CLI/Worker

- **改动内容：** 默认只读 report、有界 apply、Worker 周期执行、任务进度与失败原因查询。
- **代码位置：** `retention.py` → `maintain_retention()/retention_loop()`；`langhost/cli.py` → `retention_command()`；`production_worker.py`。
- **预期结果：** 默认 limit100、最大1000；多进程不重复推进同一任务，扫描不持远端 I/O 锁。
- **验证项：** CLI/源码契约通过；正式 CLI preview 只读；使用方并发维护只完成同一持久任务。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 代码实现完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT、FEATURES、CHANGELOG 与生产手册已同步。

### G05 源码验证

- **改动内容：** 最短生命周期测试、隔离 runner 与公开 HTTP/CLI 故障恢复验证脚本。
- **代码位置：** `tests/test_thread_retention.py`；`scripts/run_isolated_tests.py`、`scripts/verify_thread_retention.py`。
- **预期结果：** 真实 PG/Redis、重启/并发/鉴权/停维护恢复可复现。
- **验证项：** 源码392 passed/22 skipped，固定独立资源 Cron3 passed；lint/format、mypy45files、版本/锁文件通过。首次固定库冲突及修正见 Phase；skip 不计通过。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 验证代码完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT 已同步；FEATURES/CHANGELOG 跳过（测试项，能力由 G02–G04 记录）。

### G06 本地双包构建与正式发布

- **改动内容：** 双包锁步 post48、wheel/sdist 本地构建、正式 PyPI 直接上传与四产物 hash 对账。
- **代码位置：** 两包 `pyproject.toml`、`uv.lock`；[发布说明](../../releases/0.13.0/release-notes-0.13.0.post48.md)。
- **预期结果：** 正式源与本地四产物一致，无 Git/CI 发布前置。
- **验证项：** 候选 wheel23、sdist19 passed；正式四 hash 一致，源码60文件与wheel逐字节匹配；证据见 `.cache/retention-governance/*post48*`。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 构建发布完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT、FEATURES、CHANGELOG 与发布说明已同步。

### G07 正式冷安装与使用方交接

- **改动内容：** 正式源无缓存独立安装、旧包回退探针、使用方锁接入与契约/运维文档。
- **代码位置：** `scripts/verify_thread_retention.py` → `--rollback-cli`；使用方 Runtime `pyproject.toml/uv.lock`；REST 契约与生产手册。
- **预期结果：** 发布版本完整可用，恢复限制明确，平台无引擎库 TTL 扫描。
- **验证项：** 正式13项通过；使用方三服务13项、浏览器5项通过；post45 对014拒绝启动，恢复post48续跑通过。
- **状态：** `[x]` 已完成 2026-10-30。
- **合规检查：**
  - [x] 交接与文档完成
  - [x] 验证项已执行
  - [x] tasks.md 已更新
  - CONTEXT、FEATURES、CHANGELOG 已同步；既有其他专项状态保持各自验收结论。
