# 方案与取舍

## 当前事实与问题

`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py::_publish_events` 先在 PostgreSQL 记录事件，再向 Redis 扇出。`run_store.py::record_message_deltas` 对微批内每条增量各建一行，payload 包含事件和通用 trace。`models.py::RuntimeEventRow` 的 JSONB、索引与终态唯一约束支撑回放和终态判定，没有事件 TTL。`libs/langhost/src/langhost/streaming.py` 的 Run/Thread SSE 与 `protocol_api.py` 的 Protocol `since` 都读 PG 历史；`redis_stream.py` 另有有界 Redis 回放，默认 `GRAPHHARBOR_REDIS_REPLAY_MAXLEN=2000`、完成后保留约 3600 秒。平台 `apps/platform-api/src/platform_api/modules/runtime_gateway/application/service.py` 默认将 `stream_resumable` 置为 true。

本机历史清理前事件表约 7.7 GB；2.8 GB 归档的全量 topic/payload 统计因解压成本过高未完成。容量归因已经能指向无限保留的事件表，但还不能断定主因是行数、单行 payload、TOAST 或索引中的哪一项。后续测量须分别报告这些组成，且不得输出对话正文或凭据。

官方[Agent Server 架构](https://docs.langchain.com/langsmith/agent-server#persistence)将资源、checkpoint、Store 列为 PostgreSQL 持久数据，流事件由 worker 经 Redis 转发；[可恢复流配置](https://docs.langchain.com/langsmith/env-var-self-hosted#resumable_stream_ttl_seconds)描述 Redis 临时缓存默认 120 秒。[Run 创建协议](https://docs.langchain.com/langsmith/agent-server-api/stateless-runs/create-run-stream-output)将 `stream_resumable` 默认设为 false。但[Thread stream](https://docs.langchain.com/langsmith/streaming#resume-from-last-event)有 `Last-Event-ID` 回放，[Protocol 事件流](https://docs.langchain.com/langsmith/agent-server-api/streaming/protocol-v2-event-stream-sse)有 `since` 缓冲回放；在线文档可能晚于本仓锁定版本，因此不据此推断每个端点的物理保留期或内部表结构。

## 目标与非目标

目标是：运行中、HITL 恢复和 checkpoint 历史不因事件清理受损；已结束 Run 的高频原始事件不无限增长；所有支持的 SSE/SDK 重连要么完整回放，要么明确报告游标过期，绝不静默返回残缺流；PG 写入/清理有界并可观测。保留规则只使用通用 Run 状态、时间、游标和事件类型，不识别 tenant/project、模型、工具或 workspace。

不处理业务 trace 留存、聊天产品历史策略、旧运行数据迁移、token 帧聚合、事件内容压缩或替换 PostgreSQL/Redis。旧运行数据已按业务边界项目授权清空；本项目只约束新数据。是否减少**写入次数**留到保留治理测量后判断，不能用存储 TTL 冒充吞吐优化。

## 推荐路径

### 2026-09-25 实机裁决

锁定 `langgraph-api==0.13.0`、`langgraph-sdk==0.4.3` 的本机 `langgraph dev` 是 in-memory runtime。确定性完成 Run 在进程存活时，Run `stream_resumable=true` 和 Thread `Last-Event-ID: -` 可回放；Run/Thread 帧 ID 为 Redis 风格时间序号，Protocol `since` 是其会话序号。重启后 Thread/Run 资源仍返回 200，但三个流入口均不回放旧帧，仍返回 200。GraphHarbor 现有 Run/Thread/Protocol 帧与 ID 已有差异，本项目不声称修复既存协议差异；详细脱敏探针为 `tests/acceptance_app/probe_event_replay.py`。

同一确定性图的运行中/错误 Run 初探进一步发现：官方 `POST /runs` 返回 200，GraphHarbor 返回 201；官方错误 Run 流含 `metadata`、`values`、`error`，GraphHarbor 即时流只采到 `metadata`、`values`。双方最终 Run 状态均为 `error`，慢 Run 最终均为 `success`。候选 wheel 错误 Run 的 PG 复核确认第 18 条是 `terminal=true`、`status=error` 且含错误载荷，v2 Run SSE 从游标 0 重连只回放 `metadata`、`values`。源码核对：`RunRepository.fail` 将失败终态写为 `lifecycle`，`streaming._event_frame` 对 v2 Run SSE 过滤全部 `lifecycle`，因此缺失 `error` 帧属于既存映射差异，不能归因于事件清理。此处只记录差分，不把既存协议修正混入保留期治理。

因此，官方开发服务不能给出固定的 PG 保留时长。GraphHarbor 采用明确的**24 小时已结束 Run 原始事件保留期**（从 Run 最后更新时间起算），短于进程不重启时的官方开发缓存、长于其重启后零回放；该差异写入兼容资料。保留期与每批删除上限可配置，默认 24 小时和 1,000 行；reaper 每轮最多处理 20 个独立事务批次，避免单批/5 秒的吞吐上限。只删除终态 Run 的非终态流事件，保留终态行和独立的 Run/Thread/checkpoint/Store。`Last-Event-ID` 或 `since` 是已删除区间内的明确续传游标时报告 `cursor_expired`；`-`、无游标与 `since=0` 为新订阅，不承诺完整历史，调用方以 state/history 补齐。此裁决优先避免静默把**续传**伪装成完整回放。

平台旧会话恢复须经平台 API 的 state/history 与 Runtime 流入口核对，不能以旧事件永久可读为前提；浏览器操作不是本专项的验收门槛。官方生产 Redis 的 120 秒默认 TTL 仅作对照，不等同于本机 `langgraph dev` 的实际缓存生命周期。

1. **锁定事实。** 在隔离 PG17 用确定性短/长流、HITL、失败/重试 fixture 统计 topic、`pg_column_size(payload)`、表/TOAST/索引大小及单 Run 极值；必要时在磁盘空间确认后将旧归档恢复到独立库作脱敏聚合。同步对锁定官方 0.13.0 测 Run stream/join、Thread stream `Last-Event-ID`（含 `"-"`）、Protocol `since` 在运行中、终态、重启和超过缓存期后的行为。
2. **冻结回放契约。** 列出每个入口的创建条件、回放窗口、过期结果与状态/历史恢复路径。平台现有 `stream_resumable=true` 和 API 断线重连是验收消费者。以官方默认 120 秒为候选基线，但最终时间值、上限和是否需要配置项要经官方实机与平台需求确认；PG 清理窗口不得短于承诺的回放窗口。
3. **先解决无限保留。** 优先复用现有 `runtime_events` 与 `created_at` 索引，对符合保留期的已结束 Run 原始事件分批清理；运行中、排队、重试及未归属 Run 的事件不得按旧时间误删。终态行、Run 状态、checkpoint、Store 独立保留，直到其各自生命周期结束。多 worker 清理需幂等、有界，失败可重试，不能阻塞认领和 SSE。具体清理集合由第 2 步的契约决定。
4. **对齐过期和故障语义。** Run/Thread/Protocol 的旧游标均需按锁定契约处理；缺失事件不能伪装成完整重放。覆盖 Redis 不可用、PG/worker 重启、清理与订阅并发、授权撤销和跨用户请求。可恢复流丢失 Redis 缓冲时的行为须先明确，再决定是否仍要短期 PG 兜底。
5. **效果门禁。** 同一确定性负载在超过保留窗口后，过期 Run 不再保有应删除的原始增量；终态和 checkpoint 数量不变，新的等量负载不会让活跃事件行数无限累计。量测 PostgreSQL 写入延迟、扫描/删除耗时、死元组、TOAST/索引与 Redis 内存。PostgreSQL `DELETE` 不保证文件即时缩小，不能仅用 `pg_database_size` 判失败；先看活跃数据与稳定期趋势。只有容量治理仍不能满足测得的写入预算，另评审“实时事件只进短期缓冲”的后续链路改造。

## 契约、切换与回退

旧客户端的 HTTP 路径、参数、SSE 帧、顺序、ID 和授权不应改变；**可回放的历史期限**可能缩短，是必须通过官方差分明确记录的公开行为变化。`stream_resumable=false`、true、Run join、Thread `"-"`、Protocol `since` 不能用同一布尔条件代替。若参照实现对某入口承诺更久的历史，就不能按统一短 TTL 清理该入口所需数据。

先在隔离 PG17 + Redis 完成演练，再在本机候选栈启用。首次实际清理前记录数据库目标、当前 Run 状态、备份和 dry-run 待删行数；无运行任务被纳入清理后再分批执行并核对。回退代码可以停止继续清理，但**已清理的原始事件不能由 checkpoint 无损重建**；恢复须从切换前备份离线提取，并处理清理后新增事件，不能直接覆盖运行中的数据库。没有备份和过期游标验收，不开启自动清理。

## 依赖与待决项

- 锁定官方 0.13.0 的各入口时效/重连差分，以及平台实际断线窗口；这决定保留期和过期响应。
- 旧归档仅通过 `pg_restore --list` 检查，未全量恢复；是否用它做细分统计取决于隔离库空间与恢复演练，不作为实施前必须导出内容的要求。
- 业务边界项目的候选包尚未有可重复安装的正式依赖；其完整恢复、业务 Run/HITL 和文件链路未完成。事件治理的代码实施不得被误写成该项目已完成。
- 本专项以 GraphHarbor、runtime-service 和平台 API 的 HTTP/worker/数据库链路为完成门槛。真实模型仅用于必要的 `miaomiaoai` 端到端烟测；容量、故障和清理竞争用确定性图，不要求浏览器联合验收。
- 仓库既有 `dist/` runtime wheel 早于本次代码，缺迁移 009 和 `prune_expired_events`，不能用作候选。2026-09-25 由当前源码重新构建双包 `0.13.0.post32` 至 `/tmp/graphharbor-event-retention-candidate/` 并安装到独立目标目录；导入路径、版本、清理方法和迁移文件已核对，服务启动与平台联调尚未据此通过。
