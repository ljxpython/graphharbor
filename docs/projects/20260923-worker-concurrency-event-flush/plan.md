# Worker 并发与事件刷盘治理 - 整体方案

## 背景与已核实事实

外部[问题报告](/Users/lijiaxin/PyCharmMiscProject/ai-agent-platform/docs/projects/20260915-graphharbor-v3-alignment/graphharbor-worker-concurrency-and-event-flush-issue.md)记录了 `0.13.0.post30` 下不同 thread 的 run 串行排队，以及大量流式增量事件拖慢执行。当前工作树仍可确认：

- `langhost/cli.py:505` 的生产 worker 路径调用 `run_worker(config_path)`，未透传 `--n-jobs-per-worker`；`run_worker()` 只建一个 `ProductionWorker`，其 `run_forever()` 串行等待 `run_once()`。
- `RunRepository.claim_next()` 使用 `FOR UPDATE SKIP LOCKED`，并排除已有运行中 run 的 thread；跨 thread 并行有数据库基础，同 thread 串行是应保留的语义。
- `_publish_event()` 每条事件分别持久化并串行 fanout；`record_event()` 还逐条锁定 run/thread、查序号并 flush。问题报告的时间戳证明积压，但无法单独区分 PG 与 Redis 的耗时。
- 当前 v3 增量的通道是 `messages`，消息体内可出现 `content-block-delta`，不是报告建议中的顶层 `block-delta`。`_prepare_serve_env()` 当前也没有写入 `N_JOBS_PER_WORKER` 环境变量。

## 目标与边界

1. 显式设置 N 时，一个 worker 进程最多同时执行 N 个不同 thread 的 run；默认仍为 1。队列认领、租约、取消、超时和退出行为保持正确。
2. 高频事件链路在不丢事件、不改变顺序、游标和重放内容的前提下降低写入开销；具体阈值由同环境基线确定。
3. 只处理通用调度和事件传输。不得把 `miaomiaoai`、`deepseek-v4.1-flash`、模型 API 地址、密钥、业务 tool 名或业务 trace 字段加入 GraphHarbor 的 CLI、核心类型、环境变量契约、事件 payload 或数据库列。
4. 当前代码中已有 `model_id`、`platform_trace_id` 等字段，不能宣称现状已完全去业务化。此次不扩大它们；存量清理由既有业务边界分离项目负责，若修改触及同一代码段则先协调并补边界测试。

## 方案设计

本地实现和验证已完成，证据见 [verification.md](verification.md)；以下保留设计决策和生产观测边界。

### Phase 1：修复并发参数断链

- **`libs/langhost/src/langhost/cli.py`：** 将 CLI 已校验的正整数显式传给 `run_worker(config_path, n_jobs_per_worker=...)`。CLI 默认值当前为 1，因此不能声称仅设置环境变量就能覆盖默认值；若要支持环境变量配置，需单独确定 CLI 优先级并测试。
- **`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py`：** `run_worker()` 按 N 创建独立 `ProductionWorker`，每个槽位使用唯一 owner、独立 `RunRepository` 和 stop event，使用 `asyncio.gather()` 驱动。`last_transition_events` 是实例可变状态，禁止对单实例并发调用 `run_once()`。
- 仅一个槽位运行租约 reaper；信号处理通知所有槽位，等待各槽位结束当前 run 或按既有关闭语义重排后，再关闭连接池。维持现有 `claim_next()` 的同 thread 限制。
- 共享 graph registry 和 checkpointer 属于进程级资源。并发期间若触发 checkpointer 重连，应检查替换时是否会影响其他在途 run；必要时另立针对性保护，不在调度层增加业务逻辑。

### Phase 2：测量并治理事件刷盘

- 先在当前 v3 链路分别记录事件生成、PG 事务、Redis run/thread fanout、客户端首帧与终帧的耗时和数量，使用确定性高频 fixture 比较 N=1 与 N=4。避免把报告中的 27ms 全部归因于 PG。
- PG 写入采用**按 run 的有界缓冲与微批提交**：仍为每条原始事件分配独立 ID/sequence，并按原顺序持久化；达到 32 条或 50ms 时提交一次事务，提交成功后按原顺序 fanout。缓冲满时对生产者反压，PG 失败传回 run。
- `task_result`、checkpoint、run 完成/失败/取消、超时和 worker 关闭前必须完成 flush 屏障，使终态事件晚于此前增量落库。若仅复用现有逐条 `record_event()`，虽能减少事务次数，但逐条查询/flush 仍可能是瓶颈；实施时以测量决定是否需要批量分配序号与批量 INSERT，不提前改数据库模式。
- Redis 批量 fanout 使用现有 Redis pipeline：每批先写 replay stream，取得每条消息 ID，再按顺序广播 run/thread 消息。PG 已提交而 Redis 不可用时仍可从 PG 重放，沿用既有传输失败语义。没有丢弃或拼接增量。

## 链路与契约

```text
CLI -> run_worker -> N 个独立槽位 -> PostgreSQL claim_next -> LangGraph v3 stream
    -> 有序持久化 -> Redis run/thread fanout -> HTTP/SSE 与重放
```

- **对外 API/事件契约：** 无计划变更；原始事件数量、内容、每个 thread 的递增游标及终态顺序保持可观察等价。
- **配置契约：** `--n-jobs-per-worker` 在生产 worker 路径生效；默认 1。是否接纳 `N_JOBS_PER_WORKER` 环境变量作为 worker CLI 的默认来源，实施前明确并写入测试。
- **资源约束：** 并发槽位数会增加 PG/checkpointer 连接和模型请求；以现有池配置做容量测量，不能把池大小 20/10 视为无限并发承诺。

## 验收与风险

- 先用无外部模型的确定性慢图证明 N=4 时 4 个不同 thread 的 run 时间区间重叠、活跃数不超过 4；N=1 串行，同 thread 仍串行。
- 用高频 fixture 比较改动前后事件数、顺序、sequence、重放、取消/失败/关闭后的完整性与性能。性能目标详见 [verification.md](verification.md)。
- 最后使用外部验收图以 `miaomiaoai` 的 `deepseek-v4.1-flash` 做真实模型烟测。凭据只在 `~/.my_best/.env`，不读取到文档或日志；测试通过已有通用的 OpenAI 兼容客户端配置注入，GraphHarbor 核心不认识该供应商。
- 本地实现和验证已完成，未执行发布；生产环境 p95 和资源消耗需部署后单独观测。
