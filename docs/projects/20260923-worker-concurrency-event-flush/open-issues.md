# 后续治理：存量业务字段

生产 worker 的 trace context 当前读取 `model_id`、`platform_trace_id` 等字段；这与“GraphHarbor 仅承载通用 Agent Server 概念”的目标尚未完全一致。本项目修复并发和事件刷盘时不新增、依赖或扩大这些字段。

后续在 `docs/projects/20260909-graphharbor-business-boundary-separation/` 项目中核对这些字段的来源、消费者和兼容迁移，再决定移至业务侧的通用 metadata 配置或上层适配层。用户将另行评估此项；本次不顺带删除，以免改变现有 trace 契约。
