# Worker 取消传播与 LangGraph Server 对齐 - 整体方案

## 背景

平台模型可靠性链路已在 Runtime 层实现取消透传，但隔离 Worker 的默认配置复测发现：取消 ACK 时只有 primary 请求，lease 释放前仍出现 backup 请求。根因不是模型 middleware 吞掉 `CancelledError`，而是 GraphHarbor Worker 主要依赖 heartbeat 轮询，取消信号没有直接唤醒执行协程。

当前 GraphHarbor 已具备所需基础设施：PostgreSQL Run/lease、Redis control key、Redis PubSub fanout、Worker 执行 Task、reaper 和 SSE 事件持久化。需要把这些部件按 LangGraph Server Worker 的执行顺序接起来。

用户先确认方案与跨包清单，随后授权实施及验证后直接发布。当前状态done：全部Task、Final源码270/Cron3/发布包24项、静态/文档及故障/性能/安全/回退门禁通过；post42四产物已发布/PyPI复验。无Schema迁移，Git提交和现役升级不在授权范围。

## 官方对照与证据边界

| 依据 | 已核对行为 | 使用边界 |
|---|---|---|
| [官方数据面通信说明](https://docs.langchain.com/langsmith/data-plane#communication) | 取消通过 Redis string 保存信号，PubSub 通知正在执行的 Worker | 证明生产数据面的设计意图，不能替代 GraphHarbor 多进程故障验收 |
| [官方取消 Run 说明](https://docs.langchain.com/langsmith/cancel-run) | 区分 `interrupt`、`rollback` 和等待取消完成 | 在线资料可能领先于仓库锁定版本，HTTP 细节须对照锁定源码和运行结果 |
| `langgraph-api==0.13.0`：`worker.py`、`asyncio.py:wait_if_not_done()` | Worker 在 `Runs.enter()` 上下文中执行，控制事件参与执行等待，取消后等待协程收尾 | 已读 Python Worker；生产 PostgreSQL/Redis/Go 层的完整内部实现未取得 |
| `langgraph-runtime-inmem==0.33.0`：`ops.py:Runs.enter()`、`listen_for_cancellation()` | 创建 control queue，复查 control key，监听 interrupt/rollback，退出时发送 done 并移除队列 | 开发 runtime 的可读实现；不能称为生产持久化一致性证明 |
| `langgraph-api==0.13.0`：`api/runs.py:cancel_run()` | `wait=false` 返回空 body 的 202；`wait=true` 先订阅再取消，等待结果流，以 JSON StreamingResponse 返回 | post41 为 JSON 200；post42 false 已对齐空 body 202。true=200 有 Location/Content-Location，confirmed body null 与官方 pending {}、running __error__ 不同；两种锁定 SDK 丢弃 body，已真实对照 |
| Python `langgraph-sdk==0.4.3`；JS `@langchain/langgraph-sdk==1.9.28` | 仓库锁定的官方客户端调用表面 | 后续 SDK 验收使用这些版本；生产 GraphHarbor 不依赖 `langgraph-api` |

GraphHarbor 借鉴控制信号参与执行等待和“先订阅再取消”的顺序，复用自己的 PG 状态机、租约、Redis fanout 和事件存储，不复制开发 runtime 的全部实现。

## 目标

1. 默认 heartbeat 配置下，控制通知能够直接唤醒持有 lease 的 Worker，不依赖下一个轮询周期。
2. Worker 收到取消后停止通用 graph execution，正确传播原生取消；使用方验证模型退避、备用调用和实际子图随执行退出。
3. `wait=true` 的成功确认必须晚于执行清理与 lease 释放；超时或无法确认时明确返回未确认，`wait=false` 保持快速受理语义。
4. interrupt、rollback、HITL interrupt、shutdown drain、lease 丢失和自然完成之间保持不同状态机语义。
5. Redis 通知丢失、Worker 重启、API/Worker 分离和多 Worker 竞争均有持久化兜底。
6. Python/JavaScript SDK、REST/SSE 和 Platform API 适配器保持公开契约兼容。

## 非目标

- 不新增模型重试、provider fallback 或业务模型配置；这些由使用方 Runtime/Platform 项目负责。
- 不承诺撤回已经发送到外部 provider 的 HTTP 请求，也不撤回已经发生的工具副作用。
- 不引入新的公共 `cancelled` Run 状态，不把 lease/heartbeat 暴露为前端设置。
- 不在本项目内修改 `apps/platform-web` 页面；前端只按交接文档消费权威状态。

## 当前链路与缺口

```text
Platform Web
  -> Platform API adapter
  -> GraphHarbor cancel API
  -> PostgreSQL status/lease + after-commit Redis control
  -> ProductionWorker
       当前：heartbeat 轮询 -> _cancel_requested()
       目标：control queue 即时通知 + control key/PG 兜底
  -> graph execution / model middleware
  -> terminal event、lease release、SSE/Run wait
```

post41 的缺口（post42 已按下述方案修复）：

1. `ProductionWorker._heartbeat()` 才是主要取消检查入口，默认轮询约 20 秒。
2. `StreamManager` 已有 `add_control_queue()` 和跨实例 fanout，但生产 Worker 没有把它作为 Run 执行期间的取消监听。
3. `core_api._cancel_row()` 对运行中的 Run 提前写入 `interrupted` 和 terminal event；`_wait_for_run()` 因此可能立即返回。
4. `RunRepository.renew()` 当前拒绝非 running Run；提前公开 interrupted 后，清理期间可能停止续租。仅延后 lease 删除不能保证清理保护有效。
5. rollback 分支未发布即时 control，interrupt 发布受 `thread_id` 限制；`ops.py` 的字节 payload 与 Core JSON payload 必须在通用入口正确识别。
6. control queue 注册不代表跨实例 PubSub 已订阅；先读取 key 也不能独自消除读 key 与订阅之间的窗口。重连必须复查活跃 Run。

使用方已有两份优化前证据，位于 **ai-agent-platform 仓库** `docs/projects/20261006-agent-model-resilience/evidence/`：默认轮询取消 ACK 到 lease 释放 7.925 秒，ACK 后由 `[primary]` 变为 `[primary, backup]`；1 秒心跳对照为 0.705 秒且未访问备用。原 smoke 未传 `wait=true`，只能证明通知延迟，不能当作官方等待停止语义的实测。失败记录保持不变。

## 目标时序

### 取消请求

1. API 按现有授权检查和行锁读取 Run。
2. 未领取的 pending Run 直接完成取消；running Run 保留 lease，持久化取消意图，可保留既有公开 `interrupted` 投影，但此时不发布执行已停止的 terminal/done。
3. interrupt/rollback、Thread/threadless、单个/批量入口在事务提交后统一保存 Redis control key 并发布通知；PG 提交失败不得发送取消已受理，Redis 失败不回滚已经提交的取消意图。
4. `wait=false` 返回受理结果；`wait=true` 在发出取消前建立观察，并持续复查持久停止依据，避免通知先于订阅或响应连接中断造成漏确认。

### Worker 执行

1. claim 后、graph factory 启动前，注册本 Run 的 control queue；确认共享 PubSub 订阅就绪后复查 Redis control key 和 PG。订阅重建后也复查活跃 Run，覆盖先取消、注册中取消和重连期间取消。
2. 若已取消，不启动 graph factory；若 Redis 不可用，转为明确的 PG/heartbeat 兜底路径，不能把未就绪的订阅记为即时通知成功。
3. 执行期间并行等待 execution、cancel signal、shutdown 和当前 attempt timeout/运行预算。预算语义复用 [Worker 超时专项](../20261006-run-timeout-budget/README.md)，本项目不改变 attempt 重新计时规则。
4. 收到 interrupt/rollback 后取消 execution Task，等待 graph stream、上下文、可跟踪的子任务、checkpoint writer 和 event buffer 收尾；重复通知不能再次取消并打断正在进行的清理。
5. 清理期间继续按 owner + `RunLeaseRow.generation` 保护续租，不能因公开 interrupted 提前停止 heartbeat、释放 Thread 或让下个 Run claim。失去有效租约时 fence 旧写入，并报告停止未确认。
6. 清理完成后，由拥有当前代际的 Worker 在事务中确认终态、Thread 状态和 lease 释放，持久化唯一 terminal；事务提交后 fanout/done。不能先发 terminal，再靠另一个 terminal 修正“已停止”。
7. 移除 listener/queue、停止 heartbeat 并清理控制副本。发布丢失后可用 PG 回查确认；Worker crash/lease 到期则走恢复路径，不冒充 Worker 已退出的确认。

### `wait` 语义

- `wait=false`：只表示取消意图已经通过授权并持久化；通知投递失败时仍需兜底，不代表执行已经退出。
- `wait=true`：等待受当前 owner/代际保护的执行停止确认；正常路径是执行收尾完成后的持久 terminal + lease 释放，SSE/join/cancel wait 使用同一依据。提前 interrupted、lease 到期或单纯查不到 Run 都不能单独证明停止。
- `rollback`：正常路径先确认 execution 退出，再事务性恢复本 Run 的 checkpoint 基线、清理 Run 并解除保护，不能删除整个 Thread 的历史；`wait=true` 还须等待恢复事务提交。若清理异步失败，保持待清理/未确认并重试，不因 Run 删除就伪造执行停止。
- 崩溃后的 lease 到期可允许在数据库 fence 下做恢复/rollback，但它只能证明旧写入被隔离。缺少真实退出依据时，等待接口不能返回“执行停止已确认”；是否需要额外持久依据在 Task 0.1 明确。
- 等待超时、HTTP 断开与停止原因分开处理；断开等待请求不能撤销已经提交的取消。锁定官方版本的响应表面与当前 GraphHarbor 差异必须有 HTTP/SDK 证据。

### 原因与资源边界

用户 interrupt、rollback、HITL `interrupt()`、shutdown `RunControl.request_drain()`、attempt timeout、基础设施失败和 lease 丢失沿用各自 reason/恢复规则。用户已取消 Run 不得被 infrastructure retry 或 shutdown handoff 重新执行；自然完成先获事务结果时保留完成终态。

停止确认覆盖 GraphHarbor 管理的异步 execution、stream 和资源上下文。取消 asyncio Task 不能撤回已发送的 HTTP 请求，不能强制停止任意同步线程、未注册后台任务或撤销已发生的工具副作用。清理不响应时等待超时报告未确认，不能提前释放保护后宣称全部活动已停止。

## 分层改动位置

### `graphharbor-runtime`

| 文件 | 计划改动 |
|---|---|
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` | 接入 Run 级 control listener，覆盖 factory/执行/清理；区分取消、drain、timeout、lease 丢失；await 清理后完成停止确认。 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/redis_stream.py` | 复用 control queue 和共享 mux，补订阅就绪、重连复查与 listener 回收；保留 Redis 故障下的 PG 兜底。 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py` | 调整取消清理期间的 `renew()`、`finish()` 和 `requeue_expired()` 边界；校验 owner/代际，原子完成持久 terminal、Thread 和 lease。 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py` | 核对既有 `Runs.cancel()`、取消监听和 sweep；必要时复用通用处理，避免 Core/内部入口行为分叉。 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py`、`checkpoint.py` | 验证 stale writer 拒绝；正常 rollback 等执行停止，crash 恢复在 fence 后清理但不冒充停止确认。仅在现有保护不足时修改。 |
| `libs/langgraph-runtime-pg/tests/` | 复用现有 fanout、DB cancel、终态竞态和 rollback 测试，补订阅窗口、清理续租、停止确认与 crash 的确定性用例。 |

### `graphharbor`

| 文件 | 计划改动 |
|---|---|
| `libs/langhost/src/langhost/core_api.py` | 修正 `_cancel_row()`、单个/批量取消和 `_wait_for_run()`；补 rollback/threadless 通知及停止确认，对照锁定官方 HTTP 响应表面。 |
| `libs/langhost/src/langhost/streaming.py` | 只在权威执行停止/终态确认后结束 Run SSE；保留心跳和断线重连，不把心跳当取消信号。 |
| `libs/langhost/tests/` | 覆盖 `wait=false/true`、取消后流结束、重复取消、授权和 threadless Run。 |

### 使用方平台

| 层 | 责任 |
|---|---|
| Platform API | 核对 `runs.cancel` 的 `wait`/`action` 透传、上游错误和超时；不自行轮询模型候选。 |
| Runtime Service | 保持 `CancelledError`/取消信号透传，不增加 GraphHarbor 专用分支；补充“取消后无 fallback”联调。 |
| Platform Web | 不增加配置；停止按钮区分“已受理”和“已停止”，断流只恢复订阅并回查 Run。 |

## 数据与兼容策略

- PostgreSQL 是 Run 状态、lease 和终态事件的事实源；Redis control key 是可过期的取消意图副本，PubSub 只做低延迟通知。
- control listener 识别当前 Core 的 JSON `action` 和 `ops.py` 已有的 interrupt/rollback 字节 payload；这是本仓库现有入口兼容，不为了模仿官方再增加一套协议。`done` 不能当成取消，未知/畸形消息不能误停其他 Run。
- 已复用 `runtime_events`：终态 payload 的 `execution_stopped` 区分停止和 fence，rollback 的 `cancel.stopped` 与 `cancel.receipt` 回执保存清理/恢复提交事实。无表结构迁移；回退/kill 验证已证明 receipt 可持久回查，lease 到期仍不构成停止依据。
- 双包 `graphharbor` 与 `graphharbor-runtime` 必须锁步构建、测试、冷安装和发布。
- 本轮双包锁步 post42，精确依赖及 `uv.lock` 已同步；包含工作树内已验证的 Worker attempt 修订，其使用方高负载短预算限制保留在原专项，不将其标为已解决。现役平台依赖仍为 post41，待平台完整门禁后另行升级。
- 回退前停止新策略 Run，核对 pending/running/rollback Run；旧 Worker 仍可通过 control key/PG 兜底取消，但不能把旧版本当作即时取消通过。

## 风险与限制

| 风险 | 处理 |
|---|---|
| PubSub 消息丢失或订阅晚于发布 | 订阅就绪后复查 key/PG，重连复查活跃 Run；故障时记录兜底延迟，不当作健康即时通知通过。 |
| API 提前公开 `interrupted` | running 受理阶段不发停止 terminal；wait/SSE 统一核对持久停止依据。 |
| 清理期间租约失效或重复 cancel | 调整非 running 续租保护，校验代际；重复消息不得打断清理，失去保护时不返回已停止。 |
| Cancel 与自然完成/停机竞态 | RunRepository 行锁、owner/代际和单 terminal；已取消不 requeue，Thread 后续 Run 等清理。 |
| Worker 崩溃后外部请求仍可能在途 | reaper/fence 只解决存储与调度安全，不证明进程/外部活动停止。 |
| 旧/新双包混用 | 锁步发布前做兼容矩阵；旧版本只作为回退，不作为新能力验收证据。 |

## 实施顺序

1. Phase 0：冻结当前默认取消证据和官方 Worker/SDK 对照。
2. Phase 1：Runtime control listener、清理和 lease/终态语义。
3. Phase 2：Langhost cancel/wait/SSE 适配和跨包契约测试。
4. Phase 3：Platform API/Runtime 联调、双 Worker/双 API 故障验证。
5. Phase 4：双包冷安装、兼容回退、性能/安全检查和发布评审。

## 已冻结的实现边界

- `wait=false` 空 body 202 只确认意图提交；`wait=true` 只有持久停止且 lease 已释放时 200，无法确认或等待超时 503。rollback 正常恢复后删除 Run；已删除后再次 cancel 为 404，持久回执仍可查。
- SSE/join 与 cancel wait 共用 `stopped_event()`/rollback receipt；公开 `interrupted` 投影不能独自关闭流。Worker 清理与确认事务重试期间保持 owner/generation 续租，重复通知不会再次取消清理。
- HTTP/Python/JS 官方与候选各 24 场景、冷安装 24 场景、回退/kill/混合 12 场景及平台 V01-C 已通过。性能为 24 样本、并发 8、零 heartbeat tick、零失败，具体 p50/p95 与资源数见验证证据；未建立新的延迟 SLO。
- 新旧混合只保证兼容窗口的明确失败边界，不保证新版停止语义。部署须双包锁步，回退须停止新提交并完成 active/pending/rollback 对账。
