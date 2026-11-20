# GraphHarbor 本地生产运行手册

生产 profile 不依赖 `langgraph-api`、LangSmith License Key、Docker Compose 或 Kubernetes。PostgreSQL 和 Redis 必须是独立的主机服务或托管服务。

## Checkpoint单格式升级与恢复（post49 / migration015）

当前正式双包为`graphharbor==0.13.0.post49`和`graphharbor-runtime==0.13.0.post49`，数据库head为`015_checkpoint_journal`。发布包已验证不代表任何现役库已迁移。升级步骤必须由目标环境维护者在获准窗口执行：

1. 停止接收新写入，排空运行，停止所属API、Worker和维护CLI，确认没有活租约；不要让旧writer与新迁移并行。
2. 使用与数据库匹配的`pg_dump`备份完整数据库和应用配置，保存版本与校验值，先在独立新目标验证`pg_restore --single-transaction --exit-on-error`和匹配旧包的读取。备份权限限制为维护者可读，不把凭据写入命令日志。
3. 在目标安装相同正式双包，执行`graphharbor migrate upgrade`。015将旧完整基线一次性转换为最小边界、受影响键撤销日志和反向投影；无法证明归属时报告Run并回滚迁移，保留014原数据，先修复原因再前进。
4. 核对head015后启动匹配API/Worker。检查健康、原会话/审批读取、新运行、取消和允许的逆序回滚；不支持关闭Schema检查绕过门禁。新格式只接受journal-v1，旧writer写入被原子拒绝。
5. 若切换失败，停止全部所属进程，将升级前备份恢复到独立新目标并启动匹配版本，对账切换后新增数据后再决定流量。不要用旧包直接接管015、执行downgrade、清空业务表或`--clean`覆盖有新数据的现役目标。

Run边界不复制未改历史；投影反向差量仍可能随实际替换内容增大。显式`keep_latest`裁剪会关闭既有Run回滚窗口，保留Delta必要祖先并回收无引用blob；不会自动删除活跃会话历史。缺祖先时拒绝裁剪，应排查已有损坏，不能手删祖先或writes。

通用引擎隔离转换/备份恢复及正式包证据见[存储治理](../projects/20261120-checkpoint-storage-governance/verification.md)。使用方另外完成所属014→015→备份恢复014→再015；同库恢复仅针对零Run专用夹具，首次`--clean`被新外键阻止，不能作为生产恢复模板。

## Thread TTL and durable deletion (post48)

本节记录post48首次引入的TTL/可靠删除语义，历史匹配包为post48/head014；当前安装使用上节post49/head015。Thread期限和活动上报由应用决定；引擎不根据普通GET或自动Run续期。未配置策略的存量Thread不会凭旧更新时间自动过期。

应用可在`langgraph.json`声明通用异步钩子（路径仅为示例）：

```json
{
  "thread_lifecycle": {
    "before_delete": "./app.py:before_delete",
    "after_delete": "./app.py:after_delete",
    "tombstone_metadata_keys": ["tenant_id", "project_id"]
  }
}
```

钩子必须按`task_id`幂等。前置检查未结业务，后置回收资源；返回false/抛错均保留任务。任务保存的配置承诺不得通过删配置跳过。`tombstone_metadata_keys`由应用选择，仅在全部交接完成后精简metadata；必须保留授权过滤所需键，pending恢复资料和旧已完成墓碑不改。

```bash
# 已加载本环境配置后预览；默认100、上限1000。
graphharbor retention --config langgraph.json --limit 100
# 确认环境和预览结果后执行同一入口。
graphharbor retention --config langgraph.json --limit 100 --apply
```

预览只报告本轮可处理的到期Thread/待重试任务，不等同全库过期总量。Worker也运行该入口，每轮结束后等待60秒；有活跃Run、未确认Lease/停止或启用Cron时不会自动删。手动DELETE返回202表示仍清理中，用受鉴权`GET /threads/{id}/deletion`查看`state/attempts/last_error`。失败按指数退避，最大1小时；claim2分钟后可被接管。异常日志不保留应用exception正文。

删除矩阵：Thread/Run/Cron/重试/取消/原始流事件和checkpoint三表在同一事务清理；永久Run acceptance墓碑与尚需投递的webhook沿各自保留规则。`thread_deletions`保留紧凑任务墓碑，不能随意删掉以“修复卡住”。Store及应用数据由自己的保留策略处理。SQL DELETE后由autovacuum复用空间，不代表磁盘文件立即缩小。

停维护与恢复：

1. 暂停新写入，停止所属Worker及正在运行的retention CLI；只停CLI不能停Worker内的自动维护，当前无独立TTL开关。
2. 保留当前migration015、任务、墓碑、配置及PG备份；不要手改completed、删除claim或改回旧同步DELETE入口。
3. 恢复相同配置和匹配post49双包，启动所属进程；到期任务/claim过期后继续重试。失联应用资源由应用恢复，不能伪造释放凭证。原post48/014演练按当时匹配版本解释。

**不能直接回退post45接管新schema**：已实测旧包被schema head门禁拒绝启动；migration014拒绝破坏性downgrade。需要旧版灾难恢复时须从升级前备份恢复到独立目标并单独对账，不能覆盖已有新数据。已删除会话、文件不随包降级恢复。本次实际验证的是停维护保留任务、恢复post48续跑，见[验证报告](../projects/20261030-thread-retention-lifecycle/verification.md)。

## 启动顺序

```bash
export DATABASE_URI='postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/graphharbor'
export REDIS_URI='redis://127.0.0.1:6379/0'
export GRAPHHARBOR_RETRY_BASE_SECONDS=1
export GRAPHHARBOR_REAPER_INTERVAL_SECONDS=5
export GRAPHHARBOR_LEASE_SECONDS=60
# Per Worker attempt; default 86400, matching the pinned Agent Server.
export BG_JOB_TIMEOUT_SECS=300
export BG_JOB_MAX_RETRIES=3
export BG_JOB_SHUTDOWN_GRACE_PERIOD_SECS=180
export GRAPHHARBOR_RUNTIME_CONTEXT_SECRET='replace-with-a-managed-secret'
export GRAPHHARBOR_RUNTIME_CONTEXT_ISSUER='https://platform.example'
export GRAPHHARBOR_RUNTIME_CONTEXT_AUDIENCE='graphharbor-worker'

# 业务 JWT 的签发和校验由应用 auth.path 与平台负责。
# GRAPHHARBOR_RUNTIME_CONTEXT_SECRET 只签 API→worker 内部身份快照，不能复用业务 JWT 密钥。

# 只执行一次或在发布 job 中重复执行，必须先于 API/worker。
graphharbor migrate upgrade

# API
graphharbor serve --host 127.0.0.1 --port 31296 -c langgraph.json

# 公共 graph executor 完成后，另一个进程启动生产 worker
graphharbor worker --n-jobs-per-worker 1

# 当前仅可用于旧实现对比，不得用于生产
graphharbor worker --compatibility-spike --n-jobs-per-worker 1
```

生产环境设置 `GRAPHHARBOR_ENV=production` 后，数据入口需要应用通过 `auth.path`
注册 SDK Auth 认证与资源授权。GraphHarbor 只保存通用 `identity`/`permissions` 和签名
API→worker 身份快照；平台的 tenant/project、ACL、模型与工具策略由平台 Auth 和 graph 负责。
内部快照必须使用独立的 `GRAPHHARBOR_RUNTIME_CONTEXT_SECRET`，并配置配对的 issuer/audience。
候选版本在完整授权矩阵、迁移及联合验收完成前不得切换生产。

生产 worker 使用公共 LangGraph executor、PostgreSQL lease/reaper 和默认最多三个故障执行attempt；
v2 SSE、Protocol v2 和 worker 对 LangGraph `Runtime(ServerInfo)` 的身份注入已可用；v3 typed
projections、完整 graceful drain 和多实例故障验收仍必须通过后才能作为最终生产发布。

已发布post42按官方Worker attempt语义执行：`BG_JOB_TIMEOUT_SECS`默认86400，须有限正数；
`BG_JOB_MAX_RETRIES`默认3，按锁定官方Worker的最大attempt判断；停机宽限默认180、最大3600。
旧`GRAPHHARBOR_RUN_TIMEOUT_SECONDS`和`GRAPHHARBOR_SHUTDOWN_DRAIN_SECONDS`是优先别名。
每次领取有新H，排队/退避/停机不计入；本attempt工厂/执行/drain共用H，硬限唯一落timeout且不重试。
图内/provider超时未被图内policy恢复时落error；Worker只自动重试受支持的瞬时数据库故障。
正常checkpoint drain归还attempt额度，租约generation继续递增；crash/强制停止仍计入有界attempt。
这些修订随post42发布，使用方高负载短预算/完整平台接管仍以[专项](../projects/20261006-run-timeout-budget/README.md)待验边界为准，现役未升级。

## 健康检查

```bash
curl http://127.0.0.1:31296/ok
curl http://127.0.0.1:31296/info
curl http://127.0.0.1:31296/openapi.json
curl http://127.0.0.1:31296/metrics
```

`/ok` 只表示进程存活；数据库、Redis、schema 和 graph discovery 的 readiness
必须由部署探针读取应用 readiness 状态，不能把 liveness 当成可接流量证明。`/metrics`
以 Prometheus 文本格式提供 run、queue、lease/retry/reaper、SSE、HITL、PostgreSQL 和 Redis
信号；GraphHarbor runtime 日志为 JSON（含 `event`、`level`、UTC `timestamp`）。

## 停止和恢复

先停止接收新请求，再等待 worker drain；超过部署的 graceful timeout 后，未完成 run
由 PostgreSQL lease reaper 重新入队。Redis 重启不会删除 PostgreSQL 中的 run 状态、
checkpoint 或事件终态；客户端应使用最后事件 cursor 重连。

### post42 取消与回退

取消不需要调短 heartbeat。Worker 的共享 Redis control listener 负责即时唤醒，control key/PG 与 heartbeat 兜底；清理期间保持 owner/generation 续租。`wait=false` 的 202 只受理，`wait=true` 的 200 才确认受管执行退出与持久提交；503 表示停止未确认，不能靠手工删 lease 或提前 interrupted 改成成功。Redis 发布故障不撤销已提交的取消意图。

API 与 Worker 必须锁步使用 post42 双包。升级前停止新提交并对账 pending/running/rollback，等待当前 Worker drain/取消清理确认，保留 PG 备份，再切同版本进程。post42 复用现有事件表，没有 Schema 迁移；新增 `execution_stopped` 与 rollback receipt 不要求改表。

回退 post41 时同样先停止新提交，确认受管执行/rollback 恢复完成、lease 释放后切换双包；隔离演练验证原 pending Run 能由旧 Worker 继续。混合窗口中，新 API/旧 Worker 缺少停止依据返回 503，旧 API/新 Worker 可能提前 200，不具备新版保证。真实 Worker kill 后即使 lease 已回收，缺少退出证据仍只能报告未确认；已取消 Run 不得重新执行。正常 rollback 删除 Run 后重复 cancel 为 404，内部持久 receipt 可查。

验收矩阵与证据见 [取消专项](../projects/20261007-worker-cancel-propagation/verification.md)。本轮包发布授权不包含现役或远端服务升级。

2026-08-25 的本地故障验收已验证 API、worker、PostgreSQL 与 Redis 分别重启，worker 滚动
停机、队列积压和 replacement worker 接管均能完成慢 run。Redis 在运行中重启时，run 最终仍为
`success`；PostgreSQL 重启后的 checkpointer 会重连，run 按基础设施重试策略完成。重启窗口中三条
run 的持久化 `retry_count` 分别为 1、1、2，均以 `success` 终态结束。SSE 以
`Last-Event-ID: 1` 重连时返回后续事件和 `end`。

## 流事件保留与回退

候选 worker 默认在 Run 最后更新时间满 24 小时后清理其非终态原始流事件；运行中、终态、checkpoint、Store 和无 Run 事件不按此规则删除。`GRAPHHARBOR_EVENT_RETENTION_SECONDS` 默认 `86400`，设为 `0` 可停止后续清理；`GRAPHHARBOR_EVENT_PRUNE_BATCH_SIZE` 默认 `1000`，每轮 reaper 最多执行 20 个独立事务批次。`/metrics` 暴露 `graphharbor_runtime_events_pruned_total` 与 `graphharbor_runtime_event_prune_duration_ms`。调低 reaper 间隔会增加清理频率，需观察写入延迟与数据库锁等待。

清理前核对数据库目标、终态 Run 数、预计待删行数并保留 PG 备份。已删原始事件不能由 checkpoint 无损重建；回退时先将 `GRAPHHARBOR_EVENT_RETENTION_SECONDS=0` 并重启 worker，再从备份恢复到独立库，按 Run/事件 ID 离线提取，不能直接覆盖已有新事件的在线库。`DELETE` 后表文件不会立即缩小；观察活跃行、`n_dead_tup`、autovacuum 与索引大小，必要时按数据库维护窗口处理。明确过期游标应由客户端改用 Run/Thread state/history 建立新订阅；Protocol 的 HTTP 410 携带 `code=cursor_expired` 和 `recovery=thread_snapshot`。
