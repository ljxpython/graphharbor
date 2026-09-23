# Worker 并发与事件刷盘治理

## 项目概述

- **时间：** 2026-09-23 起
- **目标：** 让生产 worker 的并发参数生效，并降低高频 v3 流式事件的持久化延迟，同时保持通用 Agent Server 契约。
- **负责人：** @lijiaxin
- **状态：** done（本地开发与验证完成；`0.13.0.post32` 已发布 PyPI，生产环境性能待部署后观测）。

## 快速导航

- [整体方案](plan.md)
- [任务拆分](tasks.md)
- [验证计划](verification.md)
- [实现记录](implementation/01-worker-concurrency-and-event-batching.md)
- [存量业务字段待办](open-issues.md)
- 原始问题报告：`/Users/lijiaxin/PyCharmMiscProject/ai-agent-platform/docs/projects/20260915-graphharbor-v3-alignment/graphharbor-worker-concurrency-and-event-flush-issue.md`

## 改动范围

- **影响组件：** `langhost` worker CLI、`langgraph_runtime_pg` 生产 worker、事件持久化与协议验收。
- **改动级别：** 治理改动，涉及生产调度、持久事件顺序和关闭语义。
- **预计工作量：** 2-4 人天，事件刷盘阶段以基线测量和契约测试结果为准。

## 关键决策

1. `n_jobs_per_worker` 表示单个 worker 进程内最多同时执行的 run 数；每个执行槽位持有独立 worker 状态，继续共用进程级连接池与 graph registry。
2. 同一 thread 的 run 仍由数据库认领规则串行化；不同 thread 才参与并行执行。
3. v3 原始事件逐条保留；`messages/content-block-delta` 使用有界微批 PG 写入和 Redis pipeline fanout，不改变事件游标、重放和 SDK 可见内容。
4. GraphHarbor 不内置模型供应商、模型名、业务工具或业务字段。真实模型仅在仓库外的验收环境中使用。
5. 现存 `model_id` 等业务痕迹属于[业务边界分离项目](../20260909-graphharbor-business-boundary-separation/README.md)的治理范围；本项目不新增或依赖这些字段，实施前核对两项工作有无交叉修改。
