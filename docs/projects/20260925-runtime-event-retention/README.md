# Runtime 流事件存储与保留期治理

## 项目状态

- **目标：** 限制通用 Agent Server 的流事件长期占用，同时保持已声明的 Run/Thread/Protocol 流、重连和运行恢复语义。
- **级别：** 治理改动；影响 REST/SSE/SDK 契约、PostgreSQL 持久数据、Redis 回放和 worker 恢复。
- **状态：** `partial`。2026-09-25 已实现 24 小时保留、分批清理、水位和过期游标响应；隔离 PG17 回归覆盖成功/失败终态、checkpoint baseline/Store 保留、Redis 故障后的 PostgreSQL 回放，以及 Runtime 直连的撤权/跨用户旧游标拒绝。真实 Redis 中断发现 worker 曾误将心跳故障当作用户取消，已修复并在隔离候选 API/worker 中复测成功。平台 Thread 与 Graph 搜索链路通过；官方全入口差分、平台 API 联合撤权和容量/回退证据仍未完成。
- **与既有项目关系：** [20260923 worker 微批](../20260923-worker-concurrency-event-flush/README.md)已解决每条增量各开事务的延迟问题，但明确逐条保留原始事件；本项目处理未设保留期导致的容量问题。[业务边界解耦](../20260925-runtime-business-boundary-decoupling/README.md)保持 `partial`，其暂停与恢复入口写在原项目 README。

## 导航

- [方案与取舍](plan.md)
- [实施任务](tasks.md)
- [验收方案](verification.md)

## 已确认事实

本机 PG17 清理前有 57 Threads、324 Runs、434,742 条 `runtime_events`，Runtime 库约 7.7 GB，几乎全部在事件表；清理旧运行数据后库约 11 MB。清理前的 Runtime 归档约 2.8 GB，但尚未完整恢复；事件 topic 与 payload 体积分布未测出，不能把某一事件类型认定为唯一原因。

GraphHarbor 生产 worker 当前将每条 `messages/content-block-delta` 写成独立 JSONB 行；32 条或 50ms 的微批只合并事务。Run SSE、Thread SSE、Protocol 事件流从这张表读取历史。Redis 自身已有有界缓冲，生产路径默认最多约 2,000 条、完成后约 1 小时过期；平台网关默认请求 `stream_resumable=true`。上述本地行为与官方文档描述的 PostgreSQL 资源/checkpoint、Redis 流转及临时可恢复缓冲不能直接划等号；以锁定的 `langgraph-api==0.13.0` / `langgraph-sdk==0.4.3` 实机差分决定目标行为。

## 已实施口径与待决

已采用**已结束 Run 的 24 小时原始事件保留**，从 Run 最后更新时间起算；每事务最多删除 1,000 行，每轮 reaper 最多 20 个事务批次。运行中事件、终态、checkpoint、Store 与无 Run 事件保留。明确续传游标落入已删区间时返回 `cursor_expired`；无游标、`-`、`since=0` 是新订阅，由 state/history 补齐旧会话。官方开发服务进程重启后不回放，与本实现的 24 小时窗口不同；本项目不宣称该项完全兼容。

运行中、错误 Run 的官方差分发现既存协议差异：官方错误流含 `error` 帧，GraphHarbor v2 Run SSE 过滤持久化的失败 `lifecycle` 事件；慢 Run 创建状态码也不同。候选双 wheel 已从独立安装目录导入，在新建 PG17 库完成迁移 009，并以独立端口运行 API/worker 的确定性回放。仍待验收官方鉴权/超窗差分及平台 API 联合撤权和跨用户重连。代码实施不等于这些场景已通过。

本专项的完成门槛是 GraphHarbor、runtime-service 与平台 API 的 HTTP/worker/数据库链路；浏览器烟测仅作补充，不要求浏览器断线、HITL 或文件操作。需要真实模型时仅用 `miaomiaoai` 做端到端烟测，容量、清理竞争及故障恢复使用确定性图。平台业务边界项目自己的验收范围不随此调整。

## 规划核对

2026-09-25：规划期检查已完成；实施期的实测结果和未覆盖项以 [验收记录](verification.md) 为准。
