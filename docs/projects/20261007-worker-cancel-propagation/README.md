# Worker 取消传播与 LangGraph Server 对齐

## 项目概述

- **启动日期：** 2026-10-07。
- **目标：** 让 GraphHarbor Worker 通过 Redis 控制队列即时接收 interrupt/rollback，等待执行真正停止后再完成清理，并与锁定的 LangGraph Server `wait` 语义对齐。
- **负责人：** GraphHarbor Runtime 与 Langhost 维护者；平台 API 负责适配器联调，平台 Web 只消费既有运行状态。
- **模板类型：** 标准模板。
- **改动级别：** 治理改动，包含跨双包链路和公开取消等待语义。
- **状态：** 已完成（done）。全部Task与Final通过：源码270/Cron3、PyPI发布包矩阵24项及静态/文档门禁通过；post42双包四产物已发布并核验哈希/独立安装/CLI，协议/故障/回退/性能安全与平台V01-C证据齐全。现役仍post41，平台前端及完整平台回退另验。

## 快速导航

- [整体方案](plan.md)
- [任务拆分](tasks.md)
- [验证计划与记录](verification.md)
- [实现记录](implementation/01-worker-cancel-propagation.md)

## 人工设计确认

2026-10-07 用户明确：「我同意，写入到相关文档中，然后 GraphHarbor 建立对应的跨包任务与验证清单。」取消设计已确认，本轮范围是落文档和建立清单；不将文档交付记为代码实施或功能验收完成。

2026-10-07 用户追加：「那按照刚才的 GraphHarbor 的专项方案实施，直接使用 ~/.my_best/.env 中上传即可（开发验证完成后）。」本轮据此实施、隔离验证和直接 PyPI 发布；未授权 Git 提交、推送、标签或现役服务升级。

## 改动范围

- **`graphharbor-runtime`：** Worker 控制监听、取消清理、租约与 Redis 控制通道。
- **`graphharbor`：** cancel API、`wait=true`、SSE/Run 终态和 threadless Run 适配。
- **Platform API：** 仅核对 `wait`/`action` 透传与跨服务联调，不把取消策略做成业务配置。
- **Platform Web：** 由同事实施停止中、停止确认和确认超时展示，不新增取消配置；目标以新版停止确认与终态投影联合验收为准。

## 当前事实

- GraphHarbor `0.13.0.post41` 已有 Redis control key、PubSub fanout、PostgreSQL Run 状态和 lease。
- post41 `ProductionWorker` 主要在 heartbeat 轮询中检查取消；post42 接入共享 control listener。平台默认 heartbeat 下 ACK→lease 释放 0.123 秒，ACK/停止确认/最终均只有 primary；通用 24 样本全在首个 heartbeat 前取消，实际子图 9 项停止屏障通过。
- 使用方优化前证据：默认轮询 ACK 到 lease 释放 7.925 秒，且释放前出现备用请求；1 秒心跳对照为 0.705 秒且没有备用。原 smoke 未传 wait=true，只证明通知延迟，不能用于证明等待接口的停止确认。
- post41 `wait=true` 会把提前写入的 `interrupted` 当作完成；候选 post42 改为等待持久 terminal/rollback receipt 和执行退出，租约到期只记录 fence，不能算停止。

## 关键决策

1. PostgreSQL 是 Run 状态和租约事实源，Redis string 保存持久取消标记，Redis PubSub/控制队列负责即时唤醒；heartbeat 只续租和兜底核对。
2. Worker 在 graph factory 前注册现有控制队列，确认共享订阅就绪后复查 control key/PG，重连后复查活跃 Run。
3. 收到取消后停止通用执行并等待流、子任务和资源退出；清理期间按 owner/代际续租，之后原子完成 terminal、Thread 与 lease。wait=false 只受理，wait=true 成功须有持久停止依据。
4. 保留 `interrupt`/`rollback`、HITL、shutdown drain、lease 丢失的不同语义；不新增 `cancelled` 公共 Run 状态。
5. 取消传播不引入模型供应商、业务字段或前端取消策略配置；已发送的外部请求和已发生的工具副作用不承诺可撤回。

## 依赖和边界

- 目标对照版本：`langgraph-api==0.13.0`、`langgraph-runtime-inmem==0.33.0`、Python SDK `0.4.3`、JS SDK `1.9.28`。生产 PG/Redis/Go 内部实现未完整取得，公开文档和开发源码的证据边界见 plan.md。
- 双包锁步 `0.13.0.post42`已直接发布PyPI并独立安装复验；复用既有事件表保存停止确认与rollback回执，无Schema迁移。post41回退及混合已验：新API/旧Worker缺少停止依据时503，旧API仍可能提前200，混合版本不具备新版保证。
- 平台模型可靠性项目的前端与 Anthropic 真实 API 范围不在本项目内；Anthropic 真实 API 已由用户明确排除。
- 使用方对接入口在 **ai-agent-platform 仓库**：`docs/projects/20261006-agent-model-resilience/plan.md`（决定）、`tasks.md`（V01-C）和 `frontend-handoff.md`（同事交接）。V01-C 已通过，取消修复的发布阻塞解除；平台前端和整项回退/Final 仍未完成，现役服务仍为 post41。
