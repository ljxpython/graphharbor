# 任务清单

本文件是唯一进度来源。用户已于 2026-10-09 批准本专项与本地凭据直接发布。

- [x] G0 创建专项、读取规范、识别既有 dirty 改动；2026-10-09。
- [x] G1 原生配置、可信 context、公开协议过滤；2026-10-09。Python/JS SDK 六类入口、字段过滤、disable 与通用配置测试通过，见完成卡。
- [x] G2 原子终态与独立 delivery 迁移；2026-10-09。真实 PG 生命周期/事务/停止确认通过，见完成卡。
- [x] G3 共用可靠 sender、HMAC、claim/lease、dead-letter/replay、metrics；2026-10-09。真实 PG claim/ACK 丢失与网络安全契约通过，容量恢复归 G4。
- [/] G4 真实Worker/API与使用方跨服务验证、回归、质量、容量和回滚。引擎隔离PG/Redis、普通SDK、受管delivery、容量/积压/回退完成；正式post44最短链路通过，native定时/回填步骤完成但队列等待超时，重启未执行。正式完整矩阵资源 `blocked`，等用户通知资源可用后本地复验；使用方前端不属于引擎范围。
- [x] G5 唯一新版本双包构建、直接发布、PyPI 独立安装和文档收尾。`0.13.0.post44` 四产物 hash、CLI、migration head012、平台锁和独立安装核对通过，2026-10-09。

## 完成卡

### G1 普通与受管协议

- **改动内容：** 原生 Run/Cron webhook、启动配置与 headers 模板；普通与受管 profile 分离，可信 context 不进入公开协议。
- **代码位置：** `webhooks.py::configure_webhooks/public_kwargs`、`langhost/core_api.py`、`server.py`、`production_worker.py`。
- **预期结果：** SDK 直接使用 URL，标准 payload 与 status ACK 保持独立；应用私有终态经固定目标/纯 projector 投递。
- **验证项：** `test_webhook_sdk.py` Python/JavaScript 两种 SDK 与持久/启动锁合计 16 passed；标准字段空白名单/disable/config 在 G2/G3 回归通过。
- **状态：** [x] 已完成，2026-10-09。
- **合规检查：** [x] 实现；[x] 验证；[x] tasks；[x] CONTEXT/FEATURES/CHANGELOG 已同步；[ ] 本专项 Final（正式包使用方整链路待补，前端不属于引擎范围）。

### G2 原子终态与持久 delivery

- **改动内容：** migration012、终态同事务 snapshot；cancel/reaper 未确认时 `awaiting_stop`，确认后同 event_id 转 pending；Run/Thread 删除及 rollback 保留独立快照。
- **代码位置：** `run_store.py::record_event/confirm_fenced_stop`、`webhooks.py::snapshot_deliveries`、`checkpoint_mutations.py`、`models.py::RunCallbackDeliveryRow`。
- **预期结果：** 每 Run/profile 只有一条 delivery；未确认退出不提前投递，崩溃不产生半事务。
- **验证项：** `test_webhook_persistence.py`，及 broad 隔离回归 332 passed；2 个 cron 专用库断言在指定库重跑 3 passed。
- **状态：** [x] 已完成，2026-10-09，见 [实施记录](implementation/01-generic-webhooks.md)。
- **合规检查：** [x] 实现；[x] 验证；[x] tasks；[x] CONTEXT/FEATURES/CHANGELOG 已同步；[ ] 本专项 Final（正式包使用方整链路待补，前端不属于引擎范围）。

### G3 安全可靠投递

- **改动内容：** 通用 sender、SKIP LOCKED/lease/generation、fresh HMAC、严格受管 ACK、24h 重试/死信/重放、运维 CLI；原生 profile 与受管 profile 共用 outbox。
- **代码位置：** `webhook_delivery.py::claim_delivery/deliver_once/maintain_deliveries`、`webhooks.py::configure_webhooks`、`langhost/cli.py`。
- **预期结果：** 故障只重发 snapshot；DNS 校验后连接精确 IP、系统 TLS 验证、受管不重定向；不记录原始响应。
- **验证项：** `test_webhook_network.py`、`test_webhook_contract.py`、`test_webhook_persistence.py` 已在 332 passed 回归中通过，包括 ACK 后 crash/密钥轮换/旧 lease。
- **状态：** [x] 已完成，2026-10-09。
- **合规检查：** [x] 实现；[x] 验证；[x] tasks；[x] CONTEXT/FEATURES/CHANGELOG 已同步；[ ] 本专项 Final（正式包使用方整链路待补，前端不属于引擎范围）。

Phase与Final分开记录。正式post44产物也包含既有CAS独立路由/原子merge和migration011，正式CAS PG/HTTP四项已验；不替其他专项完成使用方验收。唯一任务状态保持partial，G4所缺是稳定本机/CI资源，不依赖Docker。
