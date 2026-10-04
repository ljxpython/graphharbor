# Worker 图级优雅停机方案

## 当前行为与目标

SIGTERM 设置 Worker `stop_event`；心跳将其转换为取消事件，运行任务被取消，Run 以 `shutdown_requeue` 回到 pending。再次领取时沿用原始输入。该行为有恢复兜底，但不会等待 LangGraph 当前 superstep 完成，也不能保证已完成节点不重复执行。

目标是在部署停机时对当前图调用 `RunControl.request_drain()`，在 `GraphDrained` 表示检查点已安全落库后重排 Run。新 Worker 用同一线程配置和空输入恢复。用户主动取消、运行超时及租约丢失仍按既有路径处理。

## 设计

1. 执行适配器接受可选 `RunControl`，传给 `ainvoke` 或 `astream_events`，不改变事件内容。
2. Worker 单独监听 `stop_event`，立即请求 drain；心跳继续续租至运行结束。收到 `GraphDrained` 后，在重排事务中记录仅服务端使用的续跑标记，并发出既有 `pending/shutdown_requeue` 事件。重新领取时跳过原始输入和固定旧 `checkpoint_id`，以当前线程检查点续跑。
3. drain 超过有限宽限期时保留现有取消重排兜底，但不得写入“已安全 drain”标记。进程硬崩溃仍交给 Lease/Reaper。
4. 不增加数据库列、新公开状态或新 SSE 事件；Run 身份、线程、鉴权上下文和事件序列保持不变。网关只验证既有投影。

## 风险与回滚

- 图内长耗时节点无法在 superstep 前停止，宽限期后仍可能重跑该节点；节点外部副作用仍需使用方保证幂等。
- 续跑只用于有线程 checkpoint 的 Run；无持久线程时退回既有重排路径。
- 代码回滚前应等待带内部续跑标记的 pending Run 完成；旧版 Worker 不认识该标记，会重新传入原输入。
- `DeltaChannel` 由使用方定义；本项目现有 checkpoint 读写及修剪测试维持兼容，不自动迁移历史线程。

## 验证环境

先运行确定性单元与适配器测试。PostgreSQL/Redis 链路仅在独立隔离测试库验证；不对包含开发数据的数据库运行 `scripts/test.sh`。
