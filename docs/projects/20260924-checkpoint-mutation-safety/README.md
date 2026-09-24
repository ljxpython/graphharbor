# Checkpoint 修剪与回滚安全治理

## 项目概述

- **时间：** 2026-09-24 开始并实施。
- **目标：** prune 不越过资源授权范围；rollback 只撤销目标 run 的持久化影响，保留合法历史，并阻止迟到写入。
- **负责人：** 项目维护者；Codex 协助规划与实施。
- **状态：** partial：核心修复与本地真实 PostgreSQL 验证完成；官方完整差分、规模性能与发布预演尚未完成。
- **改动级别：** 治理改动，涉及授权、持久数据和运行中取消。
- **影响服务：** GraphHarbor API、生产 worker、PostgreSQL checkpoint 层；无业务服务改动。
- **工作量估计：** 安全止损约 0.5–1 人天；完整回滚约 3–5 人天；含复杂 channel 的 keep_latest 另约 1–2 人天。均为初步工程量，不是交付承诺，依赖官方行为与存储验证。

## 快速导航

1. [整体方案](plan.md)：根因、分阶段修复、并发及故障约束、待验证决策。
2. [任务拆分](tasks.md)：修改文件、函数和交付出口。
3. [验证计划与记录](verification.md)：可执行场景与验收要求。
4. [调研报告](../../agent-server-compatibility-research-2026-09-24.md)：本项目的问题来源。

## 关键决策

1. 用户已明确要求一次完成完整修复，本轮已实施安全回滚与 keep_latest；不以临时禁用代替实现。
2. prune 必须把授权查询得到的 ID 传给存储层，且显式校验 strategy；不扩展到整个认证系统重构。
3. 锁定的 `langgraph-checkpoint-postgres==3.1.2` 未实现 `aprune`、`adelete_for_runs`。不能假设调用这两个方法即可修复。
4. rollback 不得调用整 thread 删除接口，不得先删 run 再赌后台清理成功。
5. 同时覆盖单个与批量 cancel；`multitask_strategy=rollback` 的完整新功能不属于本项目。
6. 优先复用已有 repository、worker、数据库事务；必要的 PostgreSQL 存储适配集中在 checkpoint 层，不引入新调度框架或自动升级核心依赖。
7. 数据破坏场景仅在独立、可丢弃的测试库执行。现有 `pg_runtime` fixture 会清空数据，不能直接对默认连接运行。

## 完成定义

只有隔离、历史保留、运行中取消、迟到写入、重启恢复、状态投影和官方对照均有证据，才将完整修复标为 done。临时安全拒绝为 partial；不支持的 channel/存储布局必须明确报告，不返回伪造成功。

## 本轮结果

详见 [实现记录](implementation/01-transactional-checkpoint-maintenance.md) 和 [验证记录](verification.md)。核心实现不新增业务字段。新增 007 迁移，API 与 worker 必须一起升级；既有运行无基线时返回 409。每次首次 claim 保存完整 checkpoint/write 基线，存在随历史增长的存储成本；共享 blobs 保留，当前不做垃圾回收。
