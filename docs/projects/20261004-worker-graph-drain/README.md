# Worker 图级优雅停机

- 状态：done（隔离 PostgreSQL/Redis 链路与双包回归通过）
- 类型：链路改动
- 范围：`graphharbor-runtime` Worker、LangGraph 执行适配、Run 状态与事件；网关消费既有事件契约
- 目标：进程停机时在 superstep 边界保存可恢复检查点，再由新 Worker 继续同一 Run。
- 边界：不修改使用方图定义、DeltaChannel、v3 事件协议或业务策略。

详见 [方案](plan.md)、[任务](tasks.md) 和 [验证](verification.md)。
