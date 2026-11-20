# Thread metadata 条件更新

> 2026-10-09发布事实补记：post44正式双包已包含CAS独立路由/原子merge，正式site-packages PG/HTTP四项通过，来源/产物核验见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。下文post43不含CAS的记录是历史基线；C03剩余使用方正式源接入待本专项核验，整体partial状态不变。

- 启动：2026-10-09。
- 状态：部分完成（partial）；使用方 F07 的 R3 已获用户批准，C01/C02 通用引擎源码与候选隔离验收已完成。C03 正式双包发布与使用方正式源接入 blocked；本轮未发布或升级现役。
- 边界：只增加通用 metadata 比较/合并，不含标题、模型或平台业务概念，不迁移 Schema。
- 任务与验收：[tasks.md](tasks.md)。使用方方案：`ai-agent-platform/docs/projects/20261009-agent-thread-auto-title/plan.md`。

契约：普通 `PATCH /threads/{thread_id}` 保持 metadata 缺省/null、ttl-only 和 minimal 204 兼容，改为数据库原子 merge。条件写走独立 `PATCH /threads/{thread_id}/metadata/cas`，必须同时提交 `metadata` 与非空 `if_metadata`；只比较列出的顶层键，JSON null 匹配缺失/null，条件失败409、资源不可见404、非法参数422。授权 filters 与条件处于同一 UPDATE，不丢失无关键。

旧引擎缺少独立路径时明确404/405，使用方不能退回可能忽略条件的普通PATCH。官方SDK无新参数，使用方走真实HTTP。源码版本暂保留post43，但2026-10-09实查PyPI正式post43没有CAS；候选四产物哈希/来源和冷安装见[verification.md](verification.md)，正式发布须新唯一双包版本。
