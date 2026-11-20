# 运行时与持久化经验库 (runtime-persistence.md)

## [坑] 只在正常结束时清缓存不能兜住 Worker 崩溃
- **场景：** ProductionWorker 跳过旧清理入口，Redis run-stream 永久驻留，SSE 全量回放放大内存。
- **正确：** Worker 终态回收本地缓冲，单条/批量写入 TTL 兜底；分页回放及时释放，未知缓存按明确授权核对后清理。
- **日期：** 2026-10-05；用户批准记录。

> 适用于 `libs/langgraph-runtime-pg/`、PostgreSQL Checkpoint、Lease 租约、Redis 任务调度与 Worker 恢复场景。

---

## [坑] 节点写 Checkpoint 误触发 CheckpointConflict
- **场景：** 官方 Worker 或无 `checkpoint_writer` 上下文执行 Run 跑图保存中间 Step Checkpoint
- **错误：** 仅凭 `not writer` 即断定为外部修改并发冲突，误拦截正在运行的 Run 自身保存状态
- **正确：** 从 `config`（`configurable`/`metadata`）提取 `run_id`，放行活跃 Run 本身的 Checkpoint 写入
- **日期：** 2026-09-30

## [坑] 子智能体工具历史被平铺导致多图状态丢失
- **场景：** 嵌套智能体（Multi-agent hierarchical teams）内部工具调用与快照回放
- **错误：** 未向 `/state/checkpoint` 传递 `checkpoint_ns`，导致子图工具轨迹全部被根图吞并或丢弃
- **正确：** 必须在请求与持久化层完整放通 `checkpoint_ns` 路由参数，子图按命名空间树形隔离存储
- **日期：** 2026-09-28

## [坑] Worker 进程崩溃导致 Run 处于永远运行死锁
- **场景：** 生产环境 Worker 异常退出或宿主机重启
- **错误：** 直接在数据库手动修改 `runs.status` 终态，导致状态机事务与事件日志不同步
- **正确：** 未取消的 Run 依赖 PostgreSQL 行级 Lease 超时，由后台 Reaper 回收并重新入队；已取消的 Run 不重执行，严禁人工改终态
- **日期：** 2026-09-25

## [坑] 通用运行时混入业务大模型专有字段
- **场景：** 为特定业务提供调试信息或链路追踪
- **错误：** 在 `Run` 模型或事件结构中增加 `model_id`、`platform_trace_id` 等业务专有字段
- **正确：** 核心包只能承载通用运行时概念，业务专有元数据一律通过 `metadata` 或使用方上层上下文透传
- **日期：** 2026-09-09

## [坑] 取消 ACK、租约到期与执行停止是不同事实

- **场景：** 默认 heartbeat、慢子图清理、Worker 故障与 wait/SSE 确认交织。
- **错误：** 仅等心跳传播取消，或把提前 interrupted、lease 消失/reaper fence 当执行退出；导致 ACK 后继续 fallback 或 wait 提前成功。
- **正确：** 持久取消意图加即时控制通知，注册/重连复查；取消后 await 受管执行/资源，清理期间按 owner/generation 续租。wait/SSE 依据持久停止及 lease 释放；仅 fence 时未确认，已取消 Run 不重执行。
- **日期：** 2026-10-07；用户明确同意记录，证据见 `docs/projects/20261007-worker-cancel-propagation/verification.md`。

## [坑] 新迁移 head 下旧包不能直接回退
- **场景：** GraphHarbor migration014 已进入数据库 head，需要验证旧 post45 的回退行为。
- **错误：** 直接切旧包、强行 `stamp` 或先破坏性 downgrade，导致旧包误接管新删除任务并丢失恢复依据。
- **正确：** 先停写入口、Worker 与 apply 维护，保留删除任务和墓碑；恢复包含当前迁移链的匹配版本。数据库 downgrade 只能走单独批准的可逆方案，不能承诺恢复已删数据。
- **日期：** 2026-10-31；证据见 `docs/projects/20261030-thread-retention-lifecycle/verification.md`。
