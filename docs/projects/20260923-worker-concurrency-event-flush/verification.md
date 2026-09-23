# Worker 并发与事件刷盘治理 - 验证计划

## 当前状态

**完成度：done（本地开发与必要验证完成，未发布）。** 原始问题证据见[外部报告](/Users/lijiaxin/PyCharmMiscProject/ai-agent-platform/docs/projects/20260915-graphharbor-v3-alignment/graphharbor-worker-concurrency-and-event-flush-issue.md)。生产环境 p95 和资源消耗需部署后观测，本记录不外推本地数据。

## 并发功能

- [x] CLI 传参、环境变量优先级、无效并发数拒绝；N=1/N=4 槽位 owner 和 repository 独立，只有一个 reaper。
- [x] PostgreSQL/Redis 集成：四个不同 thread 的 run 同时执行并成功完成；槽位数量限定为四。同一 thread 的认领限制由既有 `test_run_repository_does_not_claim_two_runs_on_one_thread` 覆盖。
- [x] 四个在途槽位同时停止后均回到 pending、释放 owner；既有测试覆盖取消、超时、租约回收与重启。
- [x] 真实 CLI API+worker 进程可启动并处理并发模型调用；未执行生产环境信号预演。

## 事件完整性与性能

- [x] 11208 条 v3 增量 fixture 在隔离 PG+本机 Redis 完成，数据库行、内容、顺序、终态和本地 thread 流 sequence 全部一致，测试主体 13.01 秒。
- [x] 同条件 1000 条事件 PG-only 对照：逐条 5.53 秒、批量 0.33 秒；事务数 1000 对 32，两边均落库 1000 行。PG+Redis 对照：逐条 7.06 秒、批量 0.65 秒，两边也均落库 1000 行。均为单次本地样本，不是生产 p95。
- [x] 批量提交后才 fanout；定时刷盘错误向 run 报告；跨实例 Redis run/thread 订阅按序收到消息；既有测试覆盖 Redis 重启后的 PG 状态和持久 SSE 重放。
- [x] 非增量、终态、取消和关闭前 flush；四槽位关闭重排测试通过。缓冲上限为 32 条，满时同步刷盘形成反压。

## 真实模型烟测

- [x] 从仓库外 `~/.my_best/.env` 向验收进程注入 `miaomiaoai` 凭据，使用 `deepseek-v4.1-flash` 发起两个不同 thread 的同时请求；二者均 success，首帧各约 0.04 秒，结束分别为 7.10 秒和 7.42 秒，两条流重叠。
- [x] 真实 LangChain Agent 完成工具调用与较长流式输出：success，首帧约 0.03 秒，终帧约 12.64 秒，`messages` 事件 459 条；不以此替代确定性性能基准。
- [x] 未在仓库、文档、测试产物或命令回显中存储密钥；供应商和模型未进入 GraphHarbor 核心参数或协议。

## 验证记录

### 2026-09-23

**环境：** 临时隔离 PostgreSQL 数据目录及数据库、本机 Redis、仓库 `uv --frozen` 环境；测试结束后关闭临时服务。真实模型通过官方 Python SDK 访问本地 `graphharbor serve` 与四槽位 `graphharbor worker`。

**测试：** 生产契约、可观测性、v3 协议、CLI、队列与公共运行时合并执行：118 passed、4 skipped。另以 `GRAPHHARBOR_TEST_DELTA_COUNT=11208` 单独运行高频测试，1 passed。`ruff check`、`ruff format --check`、`git diff --check` 通过。四个 skipped 为仓库既有条件项，未计入通过数。

**问题与修正：** 初次同时启动 API 和 worker 时，worker 提前退出，临时日志未保留，根因未确认；按迁移完成、API ready、再启动 worker 的顺序重跑成功。隔离测试未修改本机现有数据库。`record_event()` 逐条锁行/查游标/flush 的成本在初版微批后仍明显，因此补充 `record_message_deltas()`；随后补充 Redis pipeline 并重跑上述验证。

**最终结论：** `done`，本地功能、顺序、持久化和真实模型链路验证通过。未发布；生产 p95、资源消耗及实际长会话负载仍需部署后观测。
