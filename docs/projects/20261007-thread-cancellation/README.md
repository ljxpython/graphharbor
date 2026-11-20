# Thread 固定目标取消

> 2026-10-09发布事实补记：正式双包post44已包含本专项原语和migration011，来源/四产物核对见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。下文post42/post43是当时的历史证据；本专项blocked状态保留，使用方正式源接入与验收未由webhook专项核验。

2026-10-07 使用方用户批准治理方案。平台项目 `20261007-agent-run-cancellation` 的引擎配套：新增 Thread 活动 Run 原子取消与固定回执，复用 post42 Worker 退出协议，不加入平台身份或报告。

**状态：blocked。** 固定目标源码、唯一post43四产物、隔离PG/Redis/Worker、冷安装与迁移/备份/旧包回退已完成；正式PyPI发布指令与使用方正式源锁接入尚未完成。与PyPI正式post42 wheel逐项对照，确认旧包不含本次新增接口与迁移011；首轮同名post42候选只保留为历史构建证据。

新增 `POST /threads/{thread_id}/runs/cancel-active`、`GET /threads/{thread_id}/runs/cancellations/{cancellation_id}`，完整原子目标、scoped key、100项回执分页、审批快照和实际停止证明。复用 `_cancel_row()` 与现有Worker协议，不加入平台JWT、inbox或业务报告；使用方负责当前授权及安全投影。

本轮定向48项与竞争/容量26项通过；唯一post43主回归297 passed/8 skipped、Cron3 passed、Python3.12契约124 passed/4 skipped、Python3.13冷安装150 passed/4 skipped。使用方包版HTTP16场景、Showcase/DearFlow真实Docker取消、7条恢复与临时Runtime锁接入通过。500个pending目标实测约33.24秒/5524 SQL，Runtime10秒等待超时后仍恢复同取消ID，不宣称秒级。详细进度见 [tasks.md](tasks.md)，证据/复跑命令见 [verification.md](verification.md)。尚未提交、正式上传post43或升级现役进程；现役部署另行指定，不阻塞本地开发验收。
