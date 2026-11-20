# GraphHarbor 0.13.0.post48

发布日期：2026-10-30。`graphharbor-runtime`、`graphharbor` 已使用本地凭据依次直接发布正式 PyPI；无 Git commit/push/tag 或生产部署。

## 能力与边界

- Thread 支持 fixed/idle 通用 TTL（分钟）和显式 touch；普通读取、metadata 更新及运行不续期。业务期限与“哪些行为算活动”由使用方定义。
- 新增 `GET/PATCH /threads/{id}/retention`、`POST /threads/{id}/touch` 和 `GET /threads/{id}/deletion`，经过既有鉴权。官方 `ttl: {strategy: "delete", ttl: ...}` 映射 fixed；自动 keep_latest 明确拒绝，既有手动 prune keep_latest 保留。
- 手动 DELETE、prune delete、TTL 共用持久删除任务；保护活跃 Run、未确认 Lease/停止，TTL 额外保护 enabled Cron。before/after 异步 hook 有超时、持久 claim 与重试，远端 I/O 不持 SQL 锁。
- 引擎事务清理 Thread/Run/Cron、三表 checkpoint、事件/重试/取消事实；保留接受回执最小墓碑与独立回调规则。完成任务按 `tombstone_metadata_keys` 精简 metadata，pending 保留恢复资料，已有 completed 墓碑不追溯修改。
- `graphharbor retention` 默认只读，`--apply` 才执行，默认 limit 100、上限 1000；Worker 复用同一入口，每轮完成后等待 60 秒。

## 升级与恢复

双包必须同为 post48，API/Worker/CLI 使用一致 lifecycle 配置；先执行 `graphharbor migrate upgrade` 应用 migration014。存量 Thread 不从旧 updated_at 自动倒推到期，由调用方显式安装策略。

post48 没有独立 TTL 关闭开关。冻结维护需要暂停相关写入口、Worker 和 apply CLI，保留 Schema/任务/墓碑；恢复匹配版本后续跑。旧 post45 已实测不能接管014，严格 schema head 门禁会拒绝启动；014 拒绝破坏性 downgrade。已删除数据不能靠降包恢复。

配置、表级保留与操作命令见 [生产手册](../../runbooks/production-runbook.md#thread-ttl-and-durable-deletion-post48)，协议见 [REST 契约](../../standards/rest-sse-contract.md)。

## 验证

- 源码回归 392 passed / 22 skipped；固定独立资源的 Cron 套件另跑 3 passed。skip 不算通过。
- 候选 wheel 23 passed、sdist 19 passed；Ruff/format、mypy（45 个源文件）、版本锁步和锁文件检查通过。
- 正式源 `--no-cache` 冷安装后，双包版本及 CLI 验证通过；60 个 Python 源码文件与发布 wheel 逐字节一致，四产物 hash 与正式源一致。
- 正式包真实 HTTP/PG/Redis 13 项：策略/touch、只读、前后 hook 失败重试、删除屏障、重启接续、墓碑/UUID 防复活、metadata 精简、停维护保留任务、旧版降级拒绝、匹配版本恢复均通过。
- 使用方专属栈三服务 13 项和真实浏览器 5 项通过，已锁定正式 post48；未部署现役服务。

具体命令、首次失败与修正、证据路径见 [专项验证](../../projects/20261030-thread-retention-lifecycle/verification.md)。

## 正式产物 SHA256

| 产物 | SHA256 |
|---|---|
| `graphharbor-0.13.0.post48-py3-none-any.whl` | `8116854a90b788422e2d07255efba3fa91ae3ac21bca0da0d902176b93b5677a` |
| `graphharbor-0.13.0.post48.tar.gz` | `d4daca8077eedb29cef24774932a9bb9dffdef1d92b9d9a43b093e58b129bd34` |
| `graphharbor_runtime-0.13.0.post48-py3-none-any.whl` | `a375cdd2065b71c5b3573b8f3a464eacf398ba84e45168c9de4c1241b5145f24` |
| `graphharbor_runtime-0.13.0.post48.tar.gz` | `c739c12ee69b38212484194e0aecff22ad7556a743501b68f4fe697e39b6fa89` |
