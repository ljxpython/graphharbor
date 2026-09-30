# 运行时与持久化经验库 (runtime-persistence.md)

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
- **正确：** 依赖 PostgreSQL 行级 Lease 租约超时机制，由后台 Reaper 协程自动回收并重新入队，严禁人工改终态
- **日期：** 2026-09-25

## [坑] 通用运行时混入业务大模型专有字段
- **场景：** 为特定业务提供调试信息或链路追踪
- **错误：** 在 `Run` 模型或事件结构中增加 `model_id`、`platform_trace_id` 等业务专有字段
- **正确：** 核心包只能承载通用运行时概念，业务专有元数据一律通过 `metadata` 或使用方上层上下文透传
- **日期：** 2026-09-09
