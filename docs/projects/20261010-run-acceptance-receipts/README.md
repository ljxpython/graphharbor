# Run 接受回执与只读对账

- 启动日期：2026-10-10。
- 状态：**done**；任务 13/13，进度唯一事实源是 [tasks.md](tasks.md)。
- 模板：标准模板。
- 级别：治理级链路改动；涉及双包、公开 HTTP 扩展、持久化 Schema 和固定目标鉴权。
- 授权事实：2026-10-10 用户明确批准详细方案、开始实施，并要求开发验证完成后使用本地 key 直接发布 PyPI；授权包含方案中的使用方接线与正式源依赖核验，不包含现役部署或 git commit/push/tag。
- 基线：实施起点源码和正式双包均为 `0.13.0.post44`，head `012_run_callback_deliveries`。当前源码/正式双包均为 `0.13.0.post45`、head `013_run_acceptance_receipts`；四产物已上传且独立 PyPI hash 匹配，正式 wheel/sdist 冷安装与各5组进程验收通过，使用方正式源 notify/Stop/撤权联合复验通过。

## 目标与归属

解决“引擎已经提交 pending Run，但创建 ACK 丢失，平台尚无 run_id，Worker 又未运行”的对账窗口。GraphHarbor 提供通用接受事实与固定 key 的只读查询；使用方继续负责业务事件、当前权限、通知、执行前 guard 和 Stop 清理。

官方 LangGraph Server 的 Run list/get 和 metadata 可用于正向找回；上一轮通过 LangChain 文档/参考工具及发行源码核查，未发现按幂等 key 查询持久接受回执、持久否定与删除后防重的公开保证。因此本项目是 **GraphHarbor 可靠性扩展**，不能写成官方能力已经完全对齐。

来源：[平台交接文档](../../../../ai-agent-platform/docs/projects/20261009-agent-generic-production-capabilities/engine-handoff.md)。B01 已依据正式发布、使用方锁接入和联合验收解除；平台 T08 剩余矩阵、T10/F12 全栈 Final 与部署门禁由原专项继续完成。

## 已细化的决定

| 项目 | 首版决定 |
|---|---|
| 创建/回查入口 | 独立 `POST` / `GET /threads/{thread_id}/runs/acceptance`，复用既有后台 Run 创建逻辑；旧 Server 不会因忽略新 header 而创建无回执 Run |
| 请求绑定 | 必须提供原 `Idempotency-Key` 和 `GraphHarbor-Request-Digest: sha256:<hex>`；摘要覆盖最终发送的原始 UTF-8 JSON 字节 |
| 接受事实 | Run 与 accepted 回执同一 PostgreSQL 事务提交；Redis 入队和 Worker 执行不作为接受证据 |
| 查询三态 | `accepted` / `definitively_not_accepted` / `unknown`；不存在记录、请求在途、事务回滚、详情过期均不能推断未接受 |
| 持久否定 | 首版仅记录授权通过后的 `multitask_strategy=reject` 忙碌拒绝，并永久封住同 key；普通 4xx/5xx 不能直接转换成持久否定 |
| 权限 | 可信 SDK Auth 返回 opaque scope/credential 绑定；撤权后内部固定目标只读委托仍可读原回执，不能读 Run/input/state 或创建执行 |
| 保留期 | pending/running 保留完整详情；维护任务首次观察终态/Run 删除后默认保留 7 天；之后 unknown，永久保留最小防重墓碑 |
| 原生状态 | 只附带当前 Run status/是否还存在；不包含模型、工具、输出、停止确认或通知完成状态 |
| 使用方 | 发送前保存实际上游 key、最终 body 字节、引擎摘要和稳定绑定；`reconcile_only` 只 GET，Stop 后找到 run_id 仍保持 suppressed |

## 实施顺序与门禁

1. G01–G02：固定协议、摘要和 Auth 边界。
2. G03–G07：独立 ledger、原子创建、只读 HTTP、保留维护及旧调用回归。
3. U01–U03：在来源 worktree 完成使用方接线与隔离验证。
4. V01–V03：故障注入、独立安装/混合版本、联合验收与正式交接。

正式双包、使用方正式依赖锁和真实 API/Worker 两进程验收均已完成，B01 解除。PyPI 发布使用用户已授权的本地 key；现役升级未执行。

## 阅读入口

- [详细方案](plan.md)：接口、摘要、事务、授权、保留期、切换和回退。
- [任务清单](tasks.md)：13 项四段式任务、准确落点和依赖。
- [验证计划与记录](verification.md)：32 个场景、隔离环境、Phase/Final 分离。

既有 cancellation、webhook、CAS 项目状态保持各自事实源；本项目进度仅记录本轮实际实施和验证结果。
