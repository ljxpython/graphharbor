# 任务

> 2026-10-07：用户要求按LangGraph Server Worker实现；G01-G04是原设计历史。post42已随取消专项发布；用户恢复后使用方锁定/正式包12组HTTP/真实接管/匹配回退均通过，G05-G07 done。

- [x] G05：对照官方 0.13.0 Worker，按 attempt 刷新预算；Worker 硬超时 timeout，用户/provider timeout error；官方默认/配置别名及真实 PG/Redis 回归。
- [x] G06：复用 retry_counters，将故障 attempt 与递增租约代次分开；正常 checkpoint drain 不消耗故障重试额度；验证重复交接、stale owner、配置上限与普通/指定 checkpoint 重试。
- [x] G07：正式post42四产物哈希/PyPI冻结冷安装、使用方锁定与完整12组HTTP通过；真实SIGTERM后PID11475到12285，同Run新attempt H=20、checkpoint继续。匹配旧Runtime/post41组合回退保留pending/历史/工作区，普通/取消/HITL通过，无Schema迁移；旧deadline/高负载失败仅作历史。

- [x] G01：核对正式源码、已有 Worker 超时/状态机与人工批准；仅使用隔离 PG/Redis 验证。
- [x] G02：在 Run kwargs 冻结 UTC 开始/截止时间与 H；重试、恢复、shutdown 不续期。
- [x] G03：Worker 消费剩余时间，drain 不延长 H；factory 受信快照，invocation/公开结果去除私有数据。
- [x] G04：真实 PostgreSQL/Redis 的重试、取消、恢复、唯一终态和原有生产契约回归；构建双包、独立安装验证（本地候选wheel，不是PyPI发布验收）。

本专项仅包含通用底座；平台模型 middleware 与 Web 由使用方实现。

## 原阶段完成卡（10-06 历史）

| Task | 实施结果 | Phase证据 | 合规检查 |
| --- | --- | --- | --- |
| G01 | 正式源码、用户批准和隔离资源核对 | Worker/状态机与PG/Redis连通 | 批准/tasks已记录 |
| G02 | 首次claim同事务冻结，retry/requeue/reaper保留 | 真实PG预算定向16 passed；到期不执行图 | 实现、验证、tasks、CONTEXT、FEATURES、CHANGELOG已同步 |
| G03 | factory/执行/drain共享剩余时间，公开私有预算过滤 | 慢factory/shutdown、公开输出与HTTP回查 | 实现、验证、tasks、CONTEXT、FEATURES、CHANGELOG已同步 |
| G04 | 生产/持久化/公开契约100 passed/4 skipped，本地双包构建及冷安装 | 平台12组HTTP含真实SIGTERM/新PID接管 | skip未算通过；版本/证据已记录，未提交/发布 |

## 修订完成卡（10-07）

| Task | 实施结果 | Phase证据 | 合规检查 |
| --- | --- | --- | --- |
| G05 | 官方每attempt/配置/硬限与用户错误分支对齐 | 官方4项探针；真实PG/Redis116 passed/4 skipped | 实现/类型/Ruff/验证/索引已同步；skip不算通过 |
| G06 | 故障attempt与generation分离；正常drain退款；当前Run checkpoint恢复 | 六次drain与2/5次故障上限；普通/指定checkpoint调用first,second,second，定向4 passed | 无migration；不保证任意外部工具副作用绝对一次 |
| G07 | 复用正式post42，使用方双包锁定与冻结冷安装，12组HTTP/SSE与匹配版本回退 | HTTP及rollback均退出0；同Run新PID/新attempt，checkpoint/pending/历史/产物保留 | 发布哈希/任务/验证/CONTEXT/FEATURES/CHANGELOG同步；无migration，无现役部署 |

## 外部门禁

- [x] post42双包正式发布：用户另授权取消专项验证后上传，四产物已发布，哈希与独立PyPI安装/取消矩阵通过，详见 `../20261007-worker-cancel-propagation/verification.md`。
- [x] 使用方从PyPI锁定/冻结冷安装及匹配Runtime/依赖回退：当前使用方post42，旧源码/post41仅在隔离回退预演；新Runtime不能只降依赖。

G01-G04保留历史；现行G05-G07及发布/使用方接入/匹配回退门禁全部完成，本专项done。使用方前端F01-F10与联合Final仍待同事，平台整体partial；不把本底座done当成平台全链路done。
