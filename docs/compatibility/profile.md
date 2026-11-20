# GraphHarbor Compatibility Profile

## Checkpoint journal（正式post49 / migration015）

本次存储治理固定LangGraph1.2.24、checkpoint4.3.2、checkpoint-postgres3.2.1、SDK0.4.14和CLI0.4.31。Run使用首before-image与反向JSON Patch，prune关闭旧回滚窗口后保留Delta必要祖先并回收无引用blob。转换须停API/Worker并备份；仅支持journal-v1，旧进程/不匹配Schema明确拒绝，不提供在线旧格式分支。

真实PG、容量与独立安装证据见[本专项](../projects/20261120-checkpoint-storage-governance/verification.md)。本轮实际官方0.13.0严格对照仍失败：201项OpenAPI描述差异与旧正式post48逐项相同，retention扩展及thread stream额外checkpoint/debug也在旧包复现；未添加协议排除项或宣称严格等价。SSE首帧attempt竞态已复现并修正。历史2026-08-25通过字段不代表新依赖严格对照通过。

## Thread retention compatibility (0.13.0.post48)

- 官方Thread创建的 `ttl={strategy:"delete",ttl:分钟}` 映射固定到期；不将分钟误作秒。自动 `keep_latest` 显式422，手动`threads/prune`压缩保持原行为。
- `retention`、`touch`、`deletion`为GraphHarbor独立扩展路径；idle只按显式touch续期，普通GET、metadata更新和后台Run不续期。调用方必须报告自己的活动语义。
- 手动DELETE与TTL共用持久删除。完成204、未确认清理202；调用方须按任务状态判断，不能把受理等同资源已释放。
- 这不实现官方checkpointer TTL配置的全部压缩/归档能力，不提供业务Workspace或审计删除。
- migration014拒绝破坏性downgrade；旧post45因schema版本不匹配不能直接接管。已验证停维护保存任务、恢复post48继续；不承诺混合旧新包运行。

源码/候选/正式PyPI及使用方三服务证据见[生命周期专项](../projects/20261030-thread-retention-lifecycle/verification.md)；完整接口见[REST契约](../standards/rest-sse-contract.md)。本增量不改变其他专项的独立兼容门禁。

## 1. 目的

GraphHarbor 不要求第一天实现官方 Agent Server 的所有边缘能力，但必须对已经声明支持的能力做到完整兼容：

~~~text
用户的 graph 不改
langgraph.json 不改
官方 Python/JavaScript SDK 不改
官方 REST/SSE 调用不改
当前项目 custom routes/auth 不改
~~~

因此采用分层 Compatibility Profile，而不是一次性建立全量官方协议基线。

## 2. 兼容等级

### Level Core

Level Core 是第一个可交付版本的硬性范围。使用这些能力时，用户不需要增加适配层。

### Level Extended

Level Extended 是已知的官方能力，但不是第一阶段发布门禁。进入实现时必须增加独立契约和 E2E 测试。

### Level Unavailable

暂不支持的能力必须：

- 在文档和能力信息中明确标记。
- 返回清晰的 404/501 或官方对应错误。
- 不返回伪造的成功结果。
- 不改变 Core 能力的行为。
- 不对外宣称完整 Agent Server 兼容。

## 3. Level Core 范围

### 3.1 配置和发现

必须支持：

- 标准 langgraph.json。
- dependencies、graphs、env。
- auth、http.app。
- graph discovery 和 assistant/graph lookup。
- /ok、/info、/openapi.json、/docs（Scalar API Reference）。

验证：

1. 使用当前 runtime_service/langgraph.json 原文件启动。
2. 不修改 graph 导出路径。
3. 官方 SDK 能发现 assistant。
4. Studio smoke 能读取 graph 和服务信息。

### 3.2 Threads、assistants、runs

Core 覆盖当前客户端和当前项目真实链路需要的资源族：

~~~text
assistants：发现、读取、创建/更新（目标 SDK 需要的范围）
threads：创建、读取、搜索、更新、删除
thread state：读取、更新、checkpoint 读取
thread history：读取
runs：创建、读取、等待、流式运行、取消
thread runs：创建、读取、等待、流式运行、取消
~~~

具体路径、请求模型、响应模型和错误码以目标 LangGraph SDK/OpenAPI 版本为准，不凭旧版本文档猜测。

每个资源族验证：

- 正常请求。
- 参数校验失败。
- 未认证、无权限、跨租户资源。
- 资源不存在。
- 重复请求。
- 服务重启后的读取。

### 3.3 v2 普通远程流

第一阶段保留官方调用：

~~~python
client.runs.stream(
    thread_id,
    assistant_id,
    input=inputs,
    version="v2",
)
~~~

Core 覆盖：

- messages、updates、values、custom、debug。
- 多模式组合。
- token/content-block。
- final state、error、terminal event。
- SSE heartbeat。
- run 断线后的 replay/cursor。

要求：

- 官方 SDK 解析成功。
- 事件顺序稳定。
- run_id、thread_id、namespace 和 metadata 不丢失。
- 多 worker、多 API 实例行为一致。

实现边界：worker 使用一次 LangGraph `astream(version="v2")` 捕获全部标准
StreamPart 模式（`values`、`updates`、`messages`、`custom`、`checkpoints`、
`tasks`、`debug`），再通过远程 SSE 的 typed event envelope 重放。远程接口不暴露
Python `RunStream` 对象本身。

### 3.4 v3 事件流、子图和 HITL

Core 包含未来前端需要的事件能力：

~~~python
graph.stream_events(..., version="v3")
~~~

远程 server 必须提供官方客户端可消费的等价能力，不能要求前端使用 GraphHarbor 私有 parser。

必须支持：

- 主图和子图 lifecycle。
- 子图 path/namespace。
- messages、values、updates、custom、debug/events projection。
- interrupt requested、resume accepted。
- completed、failed、interrupted。
- sequence/cursor、reconnect/replay。

标准 v2 模式的 payload、`ns`/namespace、timestamp 和 interrupts 会进入 v3
projection。`stream.messages`、`stream.values`、`stream.subgraphs`、
`stream.lifecycle`、`stream.output`、`stream.interrupted`、`stream.interrupts`、
`stream.extensions` 和 `interleave()` 是图进程内的 LangGraph 对象 API；GraphHarbor
不把这些 Python 对象作为 HTTP 对象承诺。标准 `custom` mode 会转发；自定义
transformer 的 `custom:<name>` extension 只保证在调用方 LangGraph 进程内可用，
不宣称跨网络重建。

### 3.5 HITL

官方语义：

~~~text
interrupt(payload)
→ 持久化 thread/checkpoint
→ 返回 interrupt
→ 人工决定
→ Command(resume=...)
→ 同一 thread 继续
~~~

Core 必须验证：

- approve、reject、edit、respond。
- 多次顺序 interrupt。
- 多个 pending interrupt 的标识。
- 重复 resume 幂等。
- cancel 与 interrupted 区分。
- 服务重启、worker kill、客户端断线后继续。

### 3.6 当前应用边界

必须保留：

- runtime_service/langgraph.json。
- 所有当前 graph。
- 所有 custom routes。
- runtime_service/auth/platform.py 和 auth/provider.py。
- RuntimeContext 和 RuntimeRequestMiddleware 契约。
- 当前前端依赖的请求和响应语义。

### 3.7 Runtime 和运维

Core 覆盖：

- PostgreSQL threads/runs/checkpoint。
- Redis job queue、Pub/Sub、replay。
- worker lease、heartbeat、reaper。
- cancel、retry、graceful shutdown/requeue。
- 单实例和多实例。

本次Worker超时切片以`langgraph-api==0.13.0`为基线：每attempt硬限默认86400秒，下一attempt重计；硬限timeout、用户/provider TimeoutError未恢复时error，DB瞬时故障有界重试。`BG_JOB_MAX_RETRIES`按锁定源码最大attempt判断（默认3）；shutdown grace默认180/最大3600，checkpoint handoff不占故障attempt且租约代次递增。兼容官方三个BG_JOB配置，旧GraphHarbor变量作为优先别名保留。私有factory预算桥接是GraphHarbor内部集成，不是官方公开协议；累计Run总deadline不纳入本期。源码/冷安装/HTTP修订验收见[Worker专项](../projects/20261006-run-timeout-budget/README.md)，不据此宣称整套Server兼容。

post42 取消传播已按“PG 事实源 + Redis 控制 key/PubSub + Worker 停止确认”实现并通过隔离门禁。订阅就绪/重连后复查 key/PG；清理期间按 owner/generation 续租；资源退出后持久确认并释放 lease，wait/SSE/join 共用停止依据。不增加公开 cancelled 状态或业务配置，租约到期/fence 不冒充执行停止。

冻结表面：wait=false空body202；wait=true有持久停止依据200，否则超时/未确认503。confirmed body null与官方pending {}、running __error__有差异，两种SDK丢弃body；正常rollback删除后再次cancel404，receipt可查。官方/候选各24、冷安装24、回退/kill/混合12、平台默认/子图通过；post42双包已发布/PyPI/Final通过，取消专项done，见[专项](../projects/20261007-worker-cancel-propagation/verification.md)。现役未升级，不声明整套Server完全兼容。

混合版本不提供新版停止保证：新 API + 旧 Worker 缺少停止依据时 503；旧 API + 新 Worker 的 interrupt 仍可能提前 200。回退须停止新提交、完成 active/pending/rollback 对账并 drain/确认，旧 Worker 仅用作回退行为。

## 4. Level Extended 范围

以下能力不阻塞 Core 第一版，但必须保留扩展位：

~~~text
store / semantic store
cron
webhook
MCP
A2A
Generative UI
高级管理 API
多数据库/多区域 HA
~~~

进入 Extended 时必须补齐：

1. 目标版本 API/SDK 语义。
2. 认证和租户规则。
3. PostgreSQL/Redis 数据语义。
4. 单元、HTTP/SDK、multi-worker 和故障测试。
5. 文档和迁移说明。

## 5. 兼容声明

未完成 Core 前，只能声明：

~~~text
GraphHarbor Compatibility Profile: experimental
~~~

Core 完成后可以声明：

~~~text
GraphHarbor Compatibility Profile:
Core Runs + Streaming v2 + Events v3 + HITL + Subgraphs
~~~

同时必须声明 GraphHarbor、LangGraph SDK、Python、PostgreSQL、Redis 版本，以及 Extended/Unavailable 清单。

未来官方新增能力不能自动算作已支持。

当前候选的流历史期限存在明确差异：已结束 Run 的非终态原始事件在最后更新时间满 24 小时后由 worker 清理，终态和 checkpoint 保留。Run/Thread 的明确旧 `Last-Event-ID` 通过 SSE `error` 报 `cursor_expired`；Protocol 的明确旧 `since` 返回 HTTP 410、顶层 `code=cursor_expired`。无游标、Thread `Last-Event-ID: -`、Protocol `since=0` 是新订阅，旧会话应从 state/history 恢复。锁定的官方 `langgraph-api 0.13.0` 开发服务重启后即不回放，且无 `stream_resumable` 的完成 Run 不回放；GraphHarbor 当前完成 Run 仍保留窗口内事件。错误 Run 也有既存差异：官方 v2 流有 `error` 帧，GraphHarbor 的失败终态虽保存在 PG，v2 Run SSE 不输出该帧。此差异和未完成的故障/鉴权/平台 Web 验收见 [事件保留项目](projects/20260925-runtime-event-retention/README.md)，不能据此宣称全面官方兼容。

## 6. 垂直切片实施

每个切片都沿完整链路实现：

~~~text
官方客户端
→ HTTP/SSE
→ Agent Protocol server
→ auth/tenant
→ queue/worker
→ graph execution
→ PostgreSQL/Redis
→ event/replay
~~~

### Slice A：基础资源

实现：config/discovery、assistants、threads、state/history、runs。

验证：Python SDK、JavaScript SDK、REST、服务重启、PG 恢复、auth/tenant。

### Slice B：v2 远程流

实现：run stream、stream modes、Redis Pub/Sub、SSE heartbeat、replay/cursor、terminal/error event。

验证：官方 Python/JavaScript SDK不改代码；两个 API 实例；两个 worker；客户端断线重连。

### Slice C：v3 事件投影

实现：typed projections、lifecycle、subgraphs、namespace/path、custom/debug/events。

验证：主图、Deep Agent 子图、supervisor 子图、并发事件、错误事件、事件顺序。

### Slice D：HITL

实现：interrupt、checkpoint、commands/resume、approve/reject/edit/respond、resume 幂等、cancel/interrupted 状态。

验证：断线、server 重启、worker kill、多次 interrupt、重复 resume。

### Slice E：custom routes/auth/lifespan

实现：core/runtime/custom lifespan 合并、Principal、Auth、custom route auth、CORS、readiness/shutdown。

验证：所有 custom routes、401/403/404、跨租户隔离、启动顺序、关闭顺序、资源清理。

### Slice F：生产可靠性

实现：lease/reaper、retry、cancel、graceful drain/requeue、PG/Redis failure handling、multi-instance routing。

验证：worker/API kill、Redis/PG restart、rolling deployment、queue backlog。

## 7. 切片完成定义

一个切片只有同时满足以下条件才算完成：

1. server 端能力已实现。
2. runtime 数据语义已固定。
3. auth/tenant 边界已固定。
4. 官方客户端不需要修改。
5. 单元测试通过。
6. HTTP/SDK E2E 通过。
7. 故障场景通过。
8. OpenAPI/文档已更新。
9. capability/profile 已更新。
10. 未支持能力没有被误报为成功。

## 8. 已冻结的局部契约

六项 owner 决策已经完成，切片实施使用以下固定约束：

1. 目标版本主线为 Python 3.13、CI 兼容 3.11/3.12；`langgraph 1.2.11`、`langgraph-sdk 0.4.3`，`langgraph-api 0.13.0` 仅用于内部 spike。
2. Core 资源族包含当前 adapter/route/前端调用并集；`store` 保持 Extended。
3. Principal 由 platform-api delegation JWT 产生，custom routes 共用同一 Principal。
4. 对外没有独立 `cancelled` run status；cancel/HITL 均映射为官方 `interrupted`。
5. 默认 `multitask_strategy="enqueue"`，基础设施错误最多自动重试 3 次。
6. 首阶段使用本地 API、worker 与独立 PostgreSQL/Redis 部署；不提供容器编排资源。
7. P0 graph、目标并发、SSE 连接数、P95 延迟、数据保留期和旧数据迁移属于实施/运行参数，按阶段门禁补齐。

当前项目已经通过 `platform-api` adapter 使用 `/runs/batch`、`/runs/cancel`、`/threads/count`、`/threads/{thread_id}/copy`、`threads/prune` 和 cron 操作，因此这些能力属于 Core，不能再按“当前前端暂时没有点击”放入 Extended。

Cron 对齐锁定的 `langgraph-api==0.13.0` 与 `langgraph-sdk==0.4.3`：独立部署启动持久调度器；SDK 创建、搜索/计数、更新、暂停/删除、绑定 Thread 创建；`cron_id` 写入 Run 配置和元数据；时区/DST、`end_time`、`keep`/默认模式、双实例领取、故障回滚与重启恢复均有隔离库验证。认证身份在创建时保存，派发时为每次 Run 签名；撤销 API 凭据只阻断新请求，不自动停止已存在的 cron，资源所有方须主动暂停或删除。此处不承诺平台业务权限的实时撤销。

这些是切片级契约，不是重新讨论整个架构。

## 9. 当前可以开始什么

可以开始：

- 生成 Core Compatibility Profile。
- 查询最新 LangGraph 依赖。
- 建立官方 SDK smoke。
- 建立当前 server/runtime baseline。
- 做内部 compatibility spike。
- 验证 PostgreSQL、Redis 和 runtime。

不能开始：

- 宣称完整 drop-in。
- 对外发布生产版本。
- 只实现当前项目调用过的接口就宣布兼容。
- 根据旧版 langgraph-api 猜新版本协议。

## 10. 正式实施门禁

正式进入代码实施前已冻结：

~~~text
1. Core Compatibility Profile
2. 目标 LangGraph/SDK 版本
3. 当前项目真实调用清单
4. Principal/auth contract
5. run/HITL/cancel 状态机
6. 首阶段部署和验收环境
~~~

不需要提前实现官方所有协议，但这六项必须明确。

### Cron post41 接续验证（2026-10-05）

双包 post41 已发布并独立安装。有限表达式最后一次 Run 正常持久化后耗尽停用；执行 payload 编辑刷新当前受信身份，单纯启停不覆盖。通用 RunRepository.search 提供 metadata SQL 分页，未新增非标准公开 cron history API。Thread 创建并发原子唯一；Thread 已删的 queued Run 进入 error/thread_not_found 并留下 terminal event，不永久 pending。

CI 合并 126 passed/4 skipped；应用用发布包完成 22 Run（8 success、14 预期 error）隔离链路。身份/凭据/项目/Agent/模型/Thread 实时授权属于平台应用 guard，不内置到 GraphHarbor。验证覆盖锁定版本与所列场景，不表示整套 LangGraph Server 行为已穷举。

### Run接受回执 post45（2026-10-10，GraphHarbor扩展）

正式双包post45提供独立POST/GET acceptance路由，最终UTF-8字节摘要与可信固定grant、原子Run/ledger、持久忙碌拒绝和永久墓碑。官方SDK普通Run契约保持原状；新扩展由明确bytes HTTP adapter调用，不能给旧普通POST加header冒充接受事实。缺记录/过期保持unknown，不自动重投。

源码Final371/Cron3、候选wheel7/sdist5、Python SDK0.4.3/0.4.6各24与平台notify/Stop/撤权联合通过。正式四产物hash已核验，发布后独立安装和联合证据由[本专项Final](../projects/20261010-run-acceptance-receipts/verification.md)维护。不宣称LangGraph Server公开承诺同等持久否定或删除后防重。
