# Thread 到期与可靠删除方案

状态：已完成（done，7/7 任务；Phase与Final核验通过）。2026-10-30 人工批准；实现与发布证据见 [任务](tasks.md)、[验证](verification.md)。

## 已批准边界

| 所有方 | 职责 |
|---|---|
| GraphHarbor | 通用固定/闲置到期、服务端活动时间、到期扫描、删除并发保护、引擎数据清理、持久任务与幂等生命周期钩子 |
| 使用方平台 | 决定期限与续期行为、用户鉴权、上报活动、保留平台幂等与审计记录 |
| 使用方 Runtime | 未结业务检查、资源释放、Workspace 回收、跨服务清理；不直接修改引擎 Run/Lease |

核心源码不写入 30 天策略、应用表名、节点/Workspace 路径、模型或用户查看语义。

## 实现

1. 持久化 Thread 策略、最后显式活动、到期时间和删除标记；新增 migration014，无存量自动倒推到期。
2. 独立 GraphHarbor 扩展 retention/touch 接口经过既有 threads:update 鉴权。普通 GET、metadata 更新、后台 Run 不刷新闲置时间。官方 ttl:delete 保持分钟单位和固定倒计时；未实现的策略明确拒绝。
3. 手动 DELETE、批量 prune:delete、TTL 共用删除任务；任务独立于 Thread 保留进度。与 Thread 提交锁协调，标记后拒绝新工作；运行中、未确认停止/租约不作为可删事实。启用的 Cron 阻止自动到期；手动删除允许清理绑定 Cron。
4. 删除前/后均提供通用异步钩子，任务持久化重试，不持 SQL 锁等待远程 I/O；钩子接收稳定任务 ID、thread ID、metadata、原因。失败保留任务，禁止伪报已清理。配置缺失不得跳过已有任务要求的钩子。
5. 复用现有 checkpoint writer 锁，使用同一个 PostgreSQL 事务清理三张 checkpoint 表、引擎 Run/Cron/事件/取消事实并推进任务。永久 Run acceptance 墓碑与未完成回调保留各自语义。
6. 有限扫描、短事务、超时、多进程任务领取与失败重试。CLI 默认 report，apply 才写；worker 可周期执行同一维护入口。
7. 双包最终锁步 `0.13.0.post48`，本地构建四产物；使用仓库外令牌依次直接发布 Runtime、CLI。已从 PyPI 独立冷安装、核对四产物哈希/版本/CLI，正式包 13 项真实生命周期/恢复检查通过；使用方已锁定并验收。无 Git 提交/Tag/CI 发布步骤。

## 验收与回退

并发 touch/提交/删除、活跃 Run/Lease/Cron、失败重试/重启、钩子缺失、跨租户、只读预览、完整关联清理均需真实 PostgreSQL 验证。发布包重复验证核心链路。

新增 Schema 为保留存量的增量迁移，不自动为旧 Thread 安装到期策略。post45 对 migration014 的 schema head 门禁已实测拒绝启动，014 也拒绝破坏性 downgrade；不能承诺直接降旧包。冻结时暂停相关写入口、Worker 和 apply CLI（post48 无独立 TTL 关闭开关），保留删除任务和墓碑；恢复匹配 post48 后续跑已验证。不得回退为旧删除入口或声称已删除数据可以还原。

维护默认 report、limit100/最大1000；Worker 每轮完成后等待60秒。before/after hook 各30秒、任务 claim 两分钟、指数重试最长一小时；远端 I/O 不持 SQL 锁。完成墓碑按 `tombstone_metadata_keys` 精简，pending 保留恢复依据，旧 completed 不追溯修改。实际契约见 [REST 标准](../../standards/rest-sse-contract.md) 与 [生产手册](../../runbooks/production-runbook.md#thread-ttl-and-durable-deletion-post48)。

## 范围控制

本期不实现 keep_latest 自动压缩、归档恢复、通用消息总线、业务审计分区、生产部署。既有手动 keep_latest 能力保留。
