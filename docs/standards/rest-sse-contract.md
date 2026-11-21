---
status: active
last_verified: 2026-11-21
confidence: high
source_project: docs/projects/20261007-worker-cancel-propagation/
---

# Core REST 与 SSE 事件流契约标准 (rest-sse-contract.md)

本文档定义 GraphHarbor 对外暴露的 Core REST API 端点语义、SSE 流式事件协议、保活心跳机制与断线续传契约。

## Checkpoint日志与显式裁剪（post49 / migration015）

Run首次claim只记录起始head引用及必要控制状态。saver写入与首次before-image日志同事务，结果投影与标准JSON Patch反向差量同事务；回滚按Run sequence逆序检查后继依赖，原子恢复受影响行和投影，不调用使用方graph/reducer。后继已开始、活跃执行或缺少安全边界时明确拒绝，不删除Run伪装成功。

显式`threads/prune`的`keep_latest`会关闭该Thread既有Run的回滚窗口，保留每个namespace的head、逐channel所需Delta祖先/快照及pending writes，然后批量回收无引用blob。活跃Run、pending rollback或删除屏障阻止维护；缺Delta祖先返回409，整次维护事务回滚。API/CLI及内部入口共用存储服务，不新增自动裁剪或业务期限。

格式固定为journal-v1，双包须同为post50、数据库head为015。旧writer及不匹配schema拒绝启动或写入；切换采用停机备份和一次性转换，无法证明历史归属则事务失败并报告Run。恢复使用升级前备份与匹配包，不支持直接downgrade或旧包接管新格式。步骤见[运行手册](../runbooks/production-runbook.md)，正式包及容量证据见[存储治理](../projects/20261120-checkpoint-storage-governance/verification.md)。

## Thread retention 扩展（post48 / migration014）

业务期限和“什么算使用”由调用方决定。GraphHarbor 保存策略和服务端时间、扫描到期、冻结新工作并执行持久删除任务；应用钩子负责应用未结事项和资源回收。

| 接口 | 契约 |
|---|---|
| `POST /threads` | 可选 `retention={mode: "fixed"或"idle", ttl_minutes: 正数}`；与 `ttl` 互斥 |
| `GET /threads/{id}/retention` | 返回 `policy/last_activity_at/expires_at/deletion_requested_at`；授权为 `threads:read`，value含 `retention:true` |
| `PATCH /threads/{id}/retention` | 正文严格 `{policy: 策略或null}`；`threads:update`；null解除到期，不能解除已请求删除 |
| `POST /threads/{id}/touch` | 显式刷新服务端活动时间；`threads:update`，value严格 `{thread_id,touch:true}`；仅idle策略推迟到期 |
| `DELETE /threads/{id}` | 共用持久任务；全部完成204，待钩子/执行确认202并返回任务状态；未知或不在scope内404 |
| `GET /threads/{id}/deletion` | `threads:read`，按任务metadata与principal scope过滤；Thread已删仍可查任务 |

`ttl_minutes` 接受有限正数，拒绝布尔，最大52560000分钟。官方 `ttl={strategy:"delete",ttl:分钟}` 映射为fixed；自动 `keep_latest` 明确422，既有手动 `threads/prune` keep_latest不变。普通GET、metadata修改、自动Run均不续期。首次安装策略不按旧`updated_at`倒推；idle保留已知活动时间，无活动时间则从安装时计时。修改fixed策略重新计算固定到期。

删除任务为 `before_delete → after_delete → completed`，带稳定`task_id/thread_id`、原因、时间、attempts及安全错误分类；DELETE不保证同步完成。Thread删除标记与提交/touch/拷贝/prune/checkpoint写入协调；标记后拒绝新工作，墓碑阻止同UUID复活。自动到期跳过活跃Run、未确认停止/Lease及启用Cron；手动删除也等待执行退出，但可清理关联Cron定义。

`langgraph.json.thread_lifecycle` 可配置异步 `before_delete`、`after_delete` 导入路径及 `tombstone_metadata_keys` 字符串数组。钩子接收任务状态及metadata，返回false或抛错保留重试；按至少一次调用设计，应用以task_id幂等。每阶段限时30秒；远端I/O不持SQL锁。任务记录当时配置承诺，缺失或改变不得跳过。任务领取2分钟，指数退避最大1小时。

引擎事务清理Thread/Run/Cron/重试/取消/流事件及checkpoint三表，接受回执/回调保留各自语义。全部钩子确认后才完成墓碑；配置了`tombstone_metadata_keys`时只保留对应metadata。未完成任务保留恢复资料；未配置时保留metadata；不追溯改写历史已完成墓碑。选择保留键时须包含应用授权过滤所需信息。

CLI `graphharbor retention --config langgraph.json` 默认只读，`--apply`修改，`--limit`默认100且范围1–1000。Worker自动运行同一维护函数，每轮完成后等待60秒；无独立TTL关闭开关。迁移014拒绝破坏性downgrade；旧post45进程因schema head不匹配不能接管。停维护和恢复匹配post48的真实验证见[专项](../projects/20261030-thread-retention-lifecycle/verification.md)。

---

## 1. 协议核心原则

1. **官方客户端兼容**：以锁定版本的 HTTP/Python/JS 对照验证已声明支持的能力，官方 SDK 无需修改即可直连；已知响应差异在 Compatibility Profile 中明确记录。
2. **长连接保活**：网关层必须定时注入保活心跳帧，杜绝生产反向代理因长时空闲切断连接。
3. **断线精准续传**：基于事件单调递增 ID，客户端断开后通过 `Last-Event-ID` 请求头可精准补全丢失事件，避免状态重复消费。

---

## 2. SSE 事件帧结构与命名规范

所有流式响应遵循标准 `text/event-stream` 格式：

```text
id: {event_id}
event: {event_type}
data: {json_payload}

```

### 事件类型 (Event Types)
- **`metadata`**：Run 启动时的元数据描述帧（包含 run_id, assistant_id 等）。
- **`values`**：图节点产生完整状态更新时的状态帧。
- **`updates`**：图节点产生的增量状态更新帧。
- **`messages`**：流式产出的大模型 Message/Token 块。
- **`custom`**：用户图内部通过 `get_sync_writer()` 发送的自定义事件。
- **`ping` (保活心跳)**：网关内部心跳帧（见下文）。
- **`end`**：流正常结束终态标记帧。

---

## 3. 网关保活心跳机制 (Keep-Alive Heartbeat)

- **心跳间隔**：网关在流式传输期间，若连续 **15 秒**未向客户端输出有效业务事件，必须强制注入一条保活心跳帧：
  ```text
  event: ping
  data: {"timestamp": 1727670000.0}
  ```
- **客户端行为**：官方 SDK 及浏览器 EventSource 收到 `ping` 事件自动维持底层 TCP 活跃，静默丢弃或忽略该数据，不触发应用层业务报错。
- **设计防护**：彻底根治 Nginx / Ingress / ALB 默认 45~60 秒的空闲连接断开问题（消除 HTTP 504 / Connection reset by peer）。

---

## 4. 断线无感续传 (Resumption via Last-Event-ID)

- **请求头**：客户端重连时通过 HTTP Header `Last-Event-ID: {event_id}` 发起请求。
- **服务端处理**：
  1. 网关优先在 Redis 缓冲队列中检索该 ID 之后的增量事件流；
  2. 若已溢出缓存，从 PostgreSQL 事件归档表中按顺序回放；
  3. 回放完成后，无缝接入当前实时事件广播流。
- **已决审批中断处理**：对于历史已被处理并恢复的 HITL `interrupt` 事件，重播时标记 `resolved: true`，防止前端界面二次弹出死锁。

---

## 5. 错误响应标准 Envelope

当请求发生异常或未授权时，网关返回符合 Core 协议的标准 JSON 响应：

```json
{
  "message": "资源不存在或已被清理",
  "detail": {
    "error_code": "RESOURCE_NOT_FOUND",
    "thread_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
  }
}
```
- 严格遵循 HTTP 语义状态码：400 参数错误、401 未认证、403 无权限、404 未找到、409 状态并发冲突、500 服务端内部错误。

## 6. 取消受理与执行停止（post42）

- `action=interrupt/rollback, wait=false`：提交取消意图后返回空 body **202**，仅表示受理。running Run 的公开 interrupted 不等于 execution 已退出；清理期间保留 lease。
- `wait=true`：执行/stream/受管子任务/上下文退出，当前 owner/generation 提交持久停止并释放 lease 后返回 **200**；rollback 还须完成恢复事务。超时或缺少退出依据返回 **503**，取消意图继续有效。
- SSE/join 使用相同停止依据；reaper lease fence、提前 interrupted 和 Run 不存在不能独自证明停止。正常 rollback 删除 Run 后再次 cancel 为 404，持久 `cancel.receipt` 可用于内部回查。
- GraphHarbor confirmed body 为 JSON `null`；锁定官方 pending 为 `{}`，running 为 `__error__`。两种 SDK cancel 均丢弃 body，HTTP/SDK 各 24 场景已对照；这是明确的 body 差异，不宣称字节级等价。
- 仅同版本 API/Worker 提供新版确认保证。新 API/旧 Worker 缺少停止依据时 503，旧 API 仍可能提前 200；部署/回退要求见 [运行手册](../runbooks/production-runbook.md)。

证据与边界见 [Worker 取消专项](../projects/20261007-worker-cancel-propagation/verification.md)。不承诺撤销已发送的外部请求或任意同步线程。

## 7. Thread 固定目标取消（post44已包含）

新增 `POST /threads/{thread_id}/runs/cancel-active`：正文仅cancellation_id/action/reconcile_run_ids，action固定interrupt，Idempotency-Key必填1–128字符。Thread与活动Run完整NOWAIT事务快照，scoped key复用目标；冲突409整单回滚，新Run不属于旧边界。reconcile_run_ids最多100个且必须归属同Thread，供使用方对账；核心不定义inbox或业务报告。

202返回持久回执，`GET /threads/{thread_id}/runs/cancellations/{cancellation_id}` 按offset/limit读取，limit1–100；targets分页但target_count/execution_stopped覆盖完整目标。无lease加持久退出终态才确认；fence无退出证据返回confirmation_unavailable。has_pending_interrupts为接受边界快照，不表示当前审批。响应no-store。

授权由通用回调接收服务端cancel_active/cancellation_receipt标记与Thread/取消ID，回执还核对principal；使用方可只授权单一固定回执，不能凭客户端自填标记扩权。GraphHarbor不签发平台JWT，不持有产品权限/成果摘要。

新增迁移011；`0.13.0.post44` 正式双包已包含该通用原语，四产物 hash、冷安装和 migration head012 已核对。使用方包版HTTP、真实恢复及独立前端验收仍以[取消专项验证](../projects/20261007-thread-cancellation/verification.md)为准；GraphHarbor不持有平台权限或产品报告。

## 8. Thread metadata 原子更新（post44）

2026-10-09 用户批准的通用增量：普通 `PATCH /threads/{thread_id}` 使用PostgreSQL JSONB顶层原子merge，保留缺省/null metadata、ttl-only和 `Prefer:return=minimal` 的204兼容。提交null写null，不代表删除键；无关键保留，不做deep merge。

新增 `PATCH /threads/{thread_id}/metadata/cas`，正文必须有对象metadata与非空if_metadata。条件最多32个顶层键，键非空且不超过256字符；JSON值比较，null匹配缺失/null。授权metadata filters与条件同一条UPDATE；不可见/不存在404、可见但条件失效409、非法参数422；成功返回实际Thread metadata（minimal时204）。ops与公开HTTP共享同一存储helper，无数据库迁移或产品字段。

调用方必须使用独立路径，旧引擎404/405不能回退成无条件成功。`0.13.0.post44` 正式双包已包含该路由与原子merge，此增量没有Schema迁移；正式环境直接导入 site-packages 的 PG/HTTP CAS 矩阵 `4 passed`（56.848s）。源码/候选并发与使用方真实模型HTTP证据、独立使用方验收状态见[CAS专项](../projects/20261009-thread-metadata-cas/verification.md)，本发布事实不替该专项宣告 done。

## 9. Run 接受回执（post45，GraphHarbor扩展）

独立 `POST/GET /threads/{thread_id}/runs/acceptance` 要求已有规范UUID Thread目标、唯一 `Idempotency-Key`（1–256个可见ASCII字符）和 `GraphHarbor-Request-Digest: sha256:<64个小写hex>`。POST摘要覆盖最终发送的原始UTF-8 JSON字节，不能在保存摘要后重新序列化；拒绝重复JSON键、非有限数、重复header、body内幂等key、私有运行上下文、隐式建Thread、GET body/query。旧写入口收到新digest header先422，不能回退成普通POST。

SDK Auth必须认证并返回完整通用grant：`scope_id/credential_id/operation/thread_id/key_sha256/request_digest`，opaque scope/credential为64位小写hex，operation为create/read；同时需要显式命中的SDK授权handler。服务端生成回执授权marker，执行metadata filters并保存首次Thread metadata授权快照。read grant只允许固定GET回执，不能创建、读Run/state/store或取消；客户端自称binding不生效。

Run、签名和accepted ledger在同一PostgreSQL事务提交，仅首次接受注册after-commit入队；Redis成功和Worker启动不是接受证据。POST重放原回执，不改Run、签名、TTL或重复入队。首次接受/持久忙碌拒绝及同请求重放均HTTP200，通过`result`区分：

| result | 允许结论 |
|---|---|
| accepted | 原请求已持久接受，返回原run_id/accepted_at；Run已删时available=false，不推翻接受事实 |
| definitively_not_accepted | 只有通过授权与输入检查后的Thread busy/reject持久拒绝，原key永久封闭 |
| unknown | 无记录、在途/回滚或详情到期；不能据此重新POST、换key或宣称停止成功 |

同binding/key换Thread或bytes返回409；详情已过期的POST409，GET为unknown/receipt_expired。普通401/403/404/422/409/5xx均不能推导该key从未接受。响应no-store，不公开key、scope/credential、metadata快照、input/output或签名上下文。GET纯SELECT，可以附带当前Run status；接受事实不等于执行停止或通知完成。

migration013为additive ledger，无Run/Thread外键。pending/running及持有lease的Run保留详情；reaper首次观察终态/删除后默认7天，配置`GRAPHHARBOR_RUN_ACCEPTANCE_RETENTION_SECONDS`仅正整数。查询不延长期限，维护每轮最多1000、索引过滤、SKIP LOCKED和短锁复核；到期永久保留最小墓碑。013拒绝删除ledger的downgrade，代码回退须冻结受管提交、保留ledger/使用方事实并遵循匹配版本恢复步骤。墓碑线性增长，恢复到接受前备份会丢防重事实。

正式post45双包已发布；源码/候选、旧接口与两版SDK证据见[接受回执专项](../projects/20261010-run-acceptance-receipts/verification.md)。这是GraphHarbor可靠性扩展，未声称LangGraph Server公开提供同等持久否定/删除后防重保证。
