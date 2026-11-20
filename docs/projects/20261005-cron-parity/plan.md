# Cron 契约对齐方案

## 初始差距（已补齐）

隔离探针确认官方 SDK 创建、搜索、更新、删除 cron 可用；显式启动 `langgraph_api.cron_scheduler.cron_scheduler()` 后，到期可生成 pending Run。但正式 `langhost.server.create_app` 使用 `langgraph_runtime_pg.production.lifespan`，没有启动 scheduler；`core_api.cron_create` 也没有像 `ops.Crons.put` 注入 `configurable.cron_id`，实际 Run 不带可靠关联。匿名探针触发官方调度器时出现缺 `config` 的身份提取警告。当前公开的 cron 能力描述超过实际独立部署能力。

## 目标契约

对齐当前锁定的 `langgraph-api==0.13.0`、`langgraph-sdk==0.4.3`：官方 SDK 不改即可创建、搜索、计数、读取、更新、删除及绑定 Thread 创建 cron；带时区的合法表达式计算下一次时间；启停、end_time、on_run_completed 语义一致；到期运行走 GraphHarbor 正常持久 Run/worker 链路，Run 保留 `configurable.cron_id`，可按关联查询；多 API 实例不重复领取，重启后继续派发。非法 cron/时区返回对应 422，鉴权按通用资源与 Run 规则执行。

## 边界

- `libs/langhost/`：SDK/REST 参数校验、资源鉴权、payload 规范化和生命周期接线。不得从平台复制项目权限或任务标题。
- `libs/langgraph-runtime-pg/`：PG 到期领取、调度事务、队列派发与 worker 终态。不得依赖 `langgraph_api` 私有调度器作为正式实现；旧 `lifespan.py` 只是兼容性 spike。
- `ai-agent-platform`：项目权限、用户/Agent 状态、产品元数据、一次性任务、历史 UI。只通过受控网关调用 GraphHarbor；不得在平台再建周期调度器。

## 实施设计（用户已授权）

1. 将官方 `Crons.put/update` 的参数、校验、`next_run_date` 与 `cron_id` 注入行为映射到 `langhost.core_api`；Run 元数据保留稳定 `cron_id` 供查询。执行 payload 更新时以当前已认证身份刷新快照；只暂停/启用不覆盖执行身份。
2. 在 GraphHarbor 自有 runtime 实现 scheduler 生命周期：启动/取消、PG 行锁领取、推进时间和持久 Run 创建，复用现有 `RunRepository`/`enqueue_run`；不得调用官方私有 `create_valid_run`。
3. cron 创建时保存受信任身份快照，后台派发为每次 Run 签发绑定资源的 token。撤销 API 凭据阻断后续 API 调用，但不自动取消已持久化的 cron；权限系统须主动暂停或删除。此行为与官方 `user_id` 恢复 cron 运行身份的模式一致。
4. 核对派发失败时“先推进下次时间”的丢任务风险；按官方语义给出故障处理与测试，不额外承诺官方没有的 exactly-once。

## 与平台需求的关系

LangGraph cron 解决周期任务，不原生提供完整 DeerFlow 产品：once、手动触发可分别用带结束时间的策略/普通 Run 实现，但须验证边界；标题、项目可见性、撤权判断、逐次历史属于平台业务层。首期不应因此要求 GraphHarbor 增加业务表或平台角色。

## 评审门槛

这是跨包生命周期与鉴权治理改动。用户已在本会话明确授权 GraphHarbor 实施与完成任务；用户后续已明确授权使用 ~/.my_best/.env 直接发布，双包 post41 已完成 PyPI 发布。评审重点经隔离库测试覆盖：调度生命周期、身份快照与撤权边界、失败恢复、Run 关联及多实例领取。

## post41 接续实现

- 有限表达式在最后一次派发后禁用定义、next_run_date=null，保留该 Run，避免 next date 耗尽异常回滚最后一次派发。
- RunRepository.search(metadata,limit,offset) 为应用提供过滤先于分页的通用 SQL 查询，不新增公开产品历史接口。
- threads_create 使用 PostgreSQL INSERT ON CONFLICT DO NOTHING RETURNING；并发同 ID 只创建一条，保留现有授权。
- claim_next 区分 Thread 正被锁与真正缺失；已删 Thread 的 queued Run 记 error/business_error、metadata.error_code=thread_not_found。
- 缺失 Thread 时仅允许 terminal Run event 落库，event.thread_id=null 规避 FK，trace 保留原 Thread ID；仍锁定 Run 并分配 event_seq。非终态/runless 不绕过 Thread 存在检查。
- 仅补通用 Agent Server 能力；平台聚合授权、私有任务定义、once 产品表述和审批失败由 Platform API/Runtime 实施。
