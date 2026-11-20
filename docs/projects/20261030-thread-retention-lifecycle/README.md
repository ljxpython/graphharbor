# Thread 到期与可靠删除生命周期

- 启动日期：2026-10-30
- 级别：治理改动；标准模板
- 状态：已完成（done，7/7 任务；Phase与Final核验通过）
- 人工评审：2026-10-30，用户明确同意“TTL 长期归属 GraphHarbor，业务规则由平台定义”，并授权在本仓库开发、使用本地凭据直接发布双包及验证。依据为本次对话，不包含 Git 提交、推送或生产部署授权。
- 导航：[方案](plan.md) · [任务](tasks.md) · [验证](verification.md) · [实现记录](implementation/01-thread-retention.md)
- 使用方：[平台治理项目](../../../../ai-agent-platform/docs/projects/20261030-session-and-runtime-retention-governance/README.md)

GraphHarbor 负责通用 Thread 到期策略、显式活动刷新、安全删除和持久化清理交接；业务期限、用户活动解释、业务数据及磁盘资源保留在使用方。

双包 `0.13.0.post48` 已本地构建并直接发布正式 PyPI，四产物 SHA256、独立冷安装及正式包 13 项生命周期/恢复检查通过；使用方已锁定正式版本并完成隔离栈和浏览器验收。发布详情见 [post48 发布说明](../../releases/0.13.0/release-notes-0.13.0.post48.md)。

未提交 Git 或部署生产。migration014 保留任务/墓碑，旧 post45 无法接管；已验证暂停维护后恢复匹配版本续跑，不能以降包恢复已删数据。
