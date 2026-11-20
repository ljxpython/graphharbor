# Checkpoint 存储增长治理

使用方持续运行同一 Thread，需要可靠恢复和安全回滚，同时避免每个新 Run 再复制全部历史。本项目用最小运行边界和必要撤销日志替代完整基线，并在保护 Delta 祖先、pending writes 和有效回滚引用的前提下回收无引用 blob。

**当前状态：已完成（done，S01–S07）。** 最小边界、首次before-image、反向JSON Patch、引用安全prune/blob GC与一次性migration015已实现，正式双包0.13.0.post49已发布并独立无缓存冷安装；正式源42项关键测试通过、1项内部可选兼容入口未纳入。旧版10,000×16KiB夹具真实触发JSONB超限，新版六组均低于已批维护预算，不据此归因现役OOM。完整官方逐字段比较仍有既有差异，平台业务验收另记。

## 项目信息与导航

- 启动日期：2026-11-20；级别：治理改动。
- 影响范围：GraphHarbor CLI/API、PostgreSQL 运行时与 Worker。
- [整体方案](plan.md)：通用边界、目标设计、文件/函数、容量和单格式切换。
- [唯一任务进度](tasks.md)：S01–S07。
- [验证计划与记录](verification.md)：阶段验证（Phase）与整体验收（Final）分开记录。

## 通用边界与项目关系

核心只认识 Thread、Run、checkpoint、namespace、channel/version、pending writes、租约和通用维护动作。不得新增具体 Agent、模型供应商、工具、平台角色、Workspace、评测业务或业务期限；不替使用方选择 reducer 或启用活跃历史淘汰。

[平台同名关联项目](../../../../ai-agent-platform/docs/projects/20261120-checkpoint-storage-governance/README.md)负责 Agent 状态、业务政策、正式包接入与业务联合验收。跨仓链接仅供文档导航；GraphHarbor 的独立安装、测试和发布不得依赖平台源码、数据库或私有配置。

使用方已要求平台代码在其独立 Worktree 开发，全部功能和必要验收完成后迁回本地 main；准备与交付由平台 P02/P07 记录。这是平台仓库的开发安排，不新增 GraphHarbor 仓库的 Git 或生产操作授权。

[9 月安全治理项目](../20260924-checkpoint-mutation-safety/README.md)保留原设计、任务和测试。本项目独立规划和验收，不改写原状态，也不将原测试数转记为新实现通过。

## 授权与下一步

用户已同意基线放大和无引用 blob 修复方向，要求单一新实现，不加旧格式双读、长期双写或 fallback。数据完整性、当前安全回滚和并发隔离继续验收；一次性转换须有恢复证据。

本专项验证所需双包正式发布已有授权，通过源码和候选门禁后按发布规范临时读取指定位置令牌。规划不读取密钥，发包前审查工作树实际打包范围。GraphHarbor 仓库没有新增 Git、现役部署或业务数据销毁授权。

S01–S07已按批准范围完成。四产物哈希与PyPI一致、源码范围已核实，候选wheel/sdist及正式源均有独立安装证据，见[验收记录](verification.md)和[发布说明](../../releases/20261121-checkpoint-storage-post49.md)。未执行本仓Git操作或现役部署；这些事项不包含在本项目完成声明中。
