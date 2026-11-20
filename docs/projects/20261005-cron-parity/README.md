# Cron 契约对齐

- 状态：已完成（锁定 `langgraph-api==0.13.0` / `langgraph-sdk==0.4.3` 的 cron 范围）；隔离库 SDK、调度恢复、身份与租户验证通过，双包 `0.13.0.post41` 已发布并独立安装验证；有限耗尽、编辑身份、通用分页、Thread 并发创建与删除后 queued Run 终态补齐
- 目标：让 GraphHarbor 独立部署的 cron 具备已声明的 LangGraph API 同等能力
- 范围：`graphharbor` 网关与 `graphharbor-runtime` 通用调度/运行时；不加入平台任务模型
- 依据：隔离库 `graphharbor_cron_probe_20261005` 探针见 [验证](verification.md)

[方案](plan.md) · [任务](tasks.md) · [验证](verification.md)
