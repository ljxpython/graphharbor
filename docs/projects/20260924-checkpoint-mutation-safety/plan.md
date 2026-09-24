# Checkpoint 修剪与回滚安全治理：整体方案

**状态：partial。** 用户已授权完整实施；以下原始阶段方案保留作决策背景，以本节实施决策和 implementation/ 记录为准。

## 2026-09-24 实施决策

- 采用 run 首次 claim 前完整 checkpoint/write 基线，连同 thread 投影存入 `run_checkpoint_baselines`；不推测 pending writes 的 run 归属。首次捕获时间来自 PostgreSQL，用于执行先后判断。
- saver 写入先锁 run 再锁 thread，检查 owner、retry generation、有效租约和 running；基线恢复与 run 删除同一个数据库事务完成。
- running rollback 将 reason=rollback 持久化并保留租约；worker 停止并等待执行任务退出后确认，或租约过期后由 reaper 恢复。失败保留意图逐项重试。
- keep_latest 在已授权并锁定的 thread 内保留各 namespace 最新 checkpoint、其 pending writes、DeltaChannel 必要祖先；共享 blobs 不删除。维护及显式 state 写入使旧基线失效。
- 已完成 run 可在无后继依赖且基线有效时回滚；否则 409。批量按基线捕获时间逆序；批量内存在尚未停下的依赖 run 时整体 409，调用方应先完成最新运行的单次回滚再重试。
- 固定官方 in-memory 0.13.0 实测：terminal rollback 404；keep_latest 422。GraphHarbor 上述两项为 PostgreSQL 扩展，不标为严格一致。
- 当前状态 partial：核心功能与本地测试完成，完整官方边界矩阵、生产规模存储性能及发布预演未齐全。


## 1. 背景及本轮新增事实

当前主要入口是 `libs/langhost/src/langhost/core_api.py`，不是 `server.py` 中同名旧实现。

| 发现 | 证据 | 结论 |
| --- | --- | --- |
| prune 查询先做 scope 过滤，keep_latest 却传原始 thread_ids | `core_api.py::threads_prune` | 授权与实际删除对象不一致 |
| 非 delete 的任意 strategy 都进 keep_latest | 同上 | 必须先验证枚举，不能将拼写错误转成数据操作 |
| 锁定 saver 的 aprune/adelete_for_runs 均继承基类 NotImplementedError | 本轮 inspect 方法归属及源代码 | 当前默认 keep_latest 首先会失败；越权风险在可执行该方法的后端上暴露，不能声称本轮已证实默认后端发生跨租户删除 |
| rollback 先删 run，再注册整 thread 清理 | `core_api.py::_cancel_row` | 历史误删、先删记录后清理的故障窗口 |
| 提交后回调异常仅 debug 日志 | `database.py::connect` | 清理失败仍可能 HTTP 成功 |
| worker 周期查询 run 是否消失/终态后取消执行 | `ProductionWorker._cancel_requested/_heartbeat` | 删 run 不等于图已经停写 |
| checkpoint 写入走独立 psycopg pool | `checkpoint.py`、上游 saver | API SQLAlchemy 行锁不能自动隔离 saver 写入 |
| ThreadRow 缓存 values/interrupts/status/error | worker 终态更新及 state 回退 | 删 checkpoint 后也必须恢复 thread 投影 |
| 旧测试要求 fake_cleanup 接收到 thread_id | `test_run_rollback_deletes_run_and_schedules_checkpoint_cleanup` | 旧测试固化错误行为，必须改成保留历史的结果断言 |

另外发现旧兼容入口 `ops.py::Threads.prune` 的 keep_latest 分支也直接接收原始 ID；其 delete 分支走授权事件。生产修复不能拿该实现作安全 fallback。`server.py::_run_cancel` 也保留整 thread 删除，但当前生产路由挂载的是 `core_api.runs_cancel`。

## 2. 目标与范围

### 数据不变量

1. 实际变更的 thread 集合必须是当前调用者获准操作集合的子集。
2. rollback 的 checkpoint/write 变更必须可追溯到目标 run；无归属证据时禁止猜测删除。
3. A 已成功、B 待回滚：A 的 checkpoint、消息、pending writes 和所需 blobs 不丢；回滚后继续运行 C 必须正确。
4. 删除完成后 B 的旧执行者不能重新写 checkpoint/event/thread state；不能只依赖 Redis 通知或一次心跳。
5. `wait=true` 完成应表示停止与持久清理完成；失败不得伪装成功。
6. 接受回滚后 API/worker 崩溃，操作仍可恢复；不能只靠进程内任务。

### 范围边界

- 覆盖 `POST /threads/prune`、单 run cancel rollback、`POST /runs/cancel?action=rollback` 及其存储/worker链路。
- 整个 thread 的主动删除仍允许调用 `delete_thread_checkpoints`，但应改清函数注释与调用用途。
- 不重做 auth.on 全家桶、不清理业务字段、不补 cron/webhook、不实现全套 double-texting rollback。
- 不撤销 graph 已发送的邮件、支付、外部工具副作用或任意 Store 写入；这里的 rollback 是官方 run/checkpoint 范围，不能宣称通用业务事务补偿。

## 3. 官方契约与待验证边界

官方 [cancel-run](https://docs.langchain.com/langsmith/cancel-run) 明确：停止 run，删除该 run 及它创建的 checkpoints，thread 状态恢复到开始前。官方 [prune](https://docs.langchain.com/langsmith/agent-server-api/threads/prune-threads) 要求 keep_latest 保留最新可用状态。

实现基线固定 `langgraph-api==0.13.0`、LangGraph `1.2.11`、checkpoint-postgres `3.1.2`。在线文档只作补充，以下必须用固定官方服务实测后写入测试期望：

| 边界 | 需要裁决的问题 |
| --- | --- |
| 混合可见/不可见/不存在 IDs | 忽略还是整批拒绝；pruned_count 的含义；是否泄露资源存在性 |
| 空列表、重复 ID、缺字段、非法 strategy | 精确 status/body，禁止未经实测宣称全部 422 |
| pending/running/terminal rollback | 各阶段是否允许；不存在/重复 cancel 返回什么 |
| 回滚 A 后已有 B 依赖 A | 是否拒绝、裁剪后继还是其他行为；绝不能自行删除整个历史 |
| 批量取消 | 返回码、部分成功、重复请求，以及一个 thread 中多个目标的处理顺序 |
| wait=false/true | 返回时机、可见状态、后续 GET 和 timeout |

若上游行为存在数据风险，保留本地安全不变量，明确登记兼容差异，不能为“兼容”复制不安全删除。

## 4. prune 方案

### 4.1 必做的小修复

`threads_prune` 在数据库访问前校验 JSON object、thread_ids 列表与 UUID、strategy 枚举。去重后查询授权范围内的 rows，生成唯一的 `authorized_thread_ids`，两种策略只消费该列表；空授权集不调用 saver。计数由真实成功变更的授权资源计算。

维持当前 principal 的 scope 语义，不借这次任务顺带实现全资源 auth.on。但测试至少有两个 tenant/project，确保本次实际修复可被证明。

### 4.2 后端能力缺失的处理

阶段一将 `NotImplementedError` 转成明确、可测试的能力错误；拟沿旧兼容层 422 unsupported 约定，最终以固定官方实测和项目声明裁决。不得捕获所有异常后返回 pruned_count，也不得以整 thread 删除降级。

阶段二如实现 keep_latest：仅在 checkpoint PostgreSQL 适配层实现，不在 HTTP handler 拼接 SQL。先验证无活跃写入或获取统一维护屏障，再按 namespace 计算保留集。最新 checkpoint 并不必然自包含：

- 保留最新 checkpoint 对应的 pending writes。
- blobs 由 `(thread_id, checkpoint_ns, channel, version)` 共用，不随 checkpoint_id 一一对应；只能删除不再被保留状态引用的 blob，或暂缓回收孤儿 blob并明确记录存储代价。
- DeltaChannel 依赖到最近 `_DeltaSnapshot` 的祖先 checkpoint/write 链；不能简单 `DELETE WHERE checkpoint_id != max(...)`。
- 若无法证明 DeltaChannel/特殊布局的重建安全，拒绝该次修剪、不计成功。完整能力验收前必须补保留祖先或快照物化方案。

优先保留所需祖先，避免为了省空间引入复杂的状态重写器。稳定后再独立考虑 blob GC，不把它做成此次误删修复的前提。

## 5. rollback 方案

### 5.1 阶段一：封住破坏性路径

在共享 `_cancel_row` 处移除“rollback → delete_thread_checkpoints”。在完整安全执行器可用前，rollback 应在任何写入前明确拒绝，单个和批量一致；不要偷偷把 rollback 当 interrupt，也不要删 run 后才报告不支持。

这是临时安全状态，只能记为 partial。可单独交付 prune 授权修复，不必等待完整 rollback。

### 5.2 阶段二：run 级持久清理

目标顺序：

```text
校验/授权 → 持久记录 rollback 意图 → 阻止目标继续写及同 thread 抢跑
         → 取消并确认执行退出 → 精确清理目标数据
         → 重建 thread 投影 → 删除 run/释放屏障 → 返回或唤醒 waiter
```

**持久意图与恢复：** 优先复用 `RunRow.reason=rollback`、现有租约和 reaper；不新增对外 run status。实现前需证明这个状态不会被 `claim_next`/`requeue_expired` 当普通 interrupted/pending run 处理。若现有字段无法区分请求、执行停止和清理待恢复阶段，再增加最少的内部字段及迁移，不用客户端可写 kwargs 保存控制状态。新增迁移必须在评审中明确。

**写入屏障：** 单纯设置终态或等待 lease 过期不足以保证安全。checkpoint `aput/aput_writes` 和事件/终态写入必须验证目标执行者仍有写入权。推荐在现有 PostgreSQL checkpoint 适配层，采用同 thread 的数据库事务锁与 run/lease token 校验，且“校验 + 写入”在同一受保护事务内；cleanup 取得同一锁。检查必须覆盖子图与后台 pending writes。不能在 SQLAlchemy 事务里检查后去另一条连接无保护地写。

此处需要短技术验证：saver 的方法使用 pipeline/autocommit 和独立 pool，确认可安全绑定事务后再确定薄适配实现。若做不到，不得以“sleep 一个心跳周期”替代，也不扩大成通用分布式锁框架。

**停止执行：** API 持久记录意图，Redis 仅用于加速通知；worker 取消并 await 执行 task 退出后才确认。进程消失由租约/reaper接管清理；写入屏障阻止网络恢复后的旧执行者写回。后续 run、手动 state 更新和 prune 必须尊重同一维护屏障。

**目标归属：** worker 已将真实 run_id 放入 config.metadata，saver 用 `get_serializable_checkpoint_metadata` 落盘。先用真实图验证主图/子图/重试的 metadata.run_id；不能仅按时间或 UUID 大小猜测。`checkpoint_writes` 自身没有 run_id，写入可能挂在先前 checkpoint 上，需验证并补可靠归属记录或在 run 开始保存必要 write 基线，不能只删除“目标 checkpoint 对应的 writes”就宣布完成。

优先采用公开 `adelete_for_runs`，但当前版本是 stub；不自动升级核心依赖。若实现自有 PostgreSQL run cleanup，限定 `(thread_id, run_id)`、所有相关 namespaces，并审查 retained checkpoint 的 parent、delta 和 blob 引用。无法可靠归属的旧数据安全拒绝，记录具体缺口，不扩范围删除。

**投影恢复：** 清理后从存活 checkpoint/graph state 重建 `ThreadRow.values_`、interrupts、status、error、graph_id 等实际受影响字段；不能只恢复 values。维护前保存必要的原投影用于无 checkpoint 的情形，并保证源数据与恢复目标一致。已有后继 run/手动 state 分支按官方基线裁决；未验证前拒绝有依赖的历史回滚。

**原子性与失败：** 尽量把 checkpoint SQL 清理、thread 投影更新与 run 删除合并在同一 PostgreSQL 事务；若 graph state 重建必须跨连接，则采用持久意图 + 幂等阶段恢复，run 最后删除。不得继续依赖会吞异常的 after_commit callback 完成数据正确性操作。不全局修改所有 after_commit 行为，以免波及无关 Redis fanout。

**事件与关联资源：** 核对 RunLeaseRow、RuntimeEventRow、Redis replay/control、thread.event_seq；不重置 thread 单调序号、不让旧 success 覆盖 rollback。按官方对照确定已发事件和剩余持久事件的可见语义，保证活动流可结束。移除 run 不应遗留永久 busy 状态或不可结束的 waiter。

**批量：** `runs_cancel`、`runs_cancel_many` 都调用同一服务。先完成范围/能力校验，按稳定 thread/run 顺序加锁，避免死锁。不要求跨 thread 分布式原子事务，但必须验证与声明部分失败语义，不能静默吞失败。

## 6. 文件与调用链

| 文件/函数 | 计划修改 |
| --- | --- |
| `langhost/core_api.py::threads_prune` | 校验、授权结果集、错误与计数 |
| `langhost/core_api.py::_cancel_row/runs_cancel/runs_cancel_many` | 统一 rollback 入口、持久请求、wait 与结果语义 |
| `langgraph_runtime_pg/checkpoint.py` | 整 thread 与 run 删除分离、薄 PG adapter、能力探测、写入屏障 |
| `langgraph_runtime_pg/production_worker.py` | 取消确认、run/write 归属、cleanup 与恢复 |
| `langgraph_runtime_pg/run_store.py` | 维护状态下 claim/reap/finish 的一致性；共享锁顺序 |
| `langgraph_runtime_pg/models.py` 与 migration | 仅在现有字段不足以承载可靠恢复时增加最少内部状态/归属字段 |
| `langhost/server.py::_run_cancel` | 查明外部/测试调用；旧实现改委托共享路径或移除前确认，禁止残留危险备用入口 |
| `langgraph_runtime_pg/ops.py::Threads.prune` | 核对旧 profile 授权；不能成为替代生产安全限制的绕路 |
| production/persistence 测试与验收 fixture | 替换旧误删断言、补真实 saver 和并发回归 |

先全仓检索调用者，再决定是否删除旧函数。GraphHarbor 核心无需知道任何模型、供应商或业务工具。

## 7. 风险、部署与回退

- 已有工作树有其他修改，实施时只改本项目相关行，不覆盖 v3 等既存工作。
- 上游表结构是锁定版本的存储契约；自有 SQL 必须有真实 Postgres 测试和版本升级门禁。
- 旧数据缺少 run/write 归属时不做猜测性批量迁移；记录无法完整回滚的范围与后续迁移选项。
- 若新增状态/屏障，新旧 worker 混跑可能绕过检查；发布应停止旧 worker、完成必要迁移后统一切换，再开放 destructive API。
- 回退到“安全拒绝”，不能回退到旧整 thread 删除逻辑。代码回退不能恢复误删数据，实施前保留测试数据快照和恢复演练。
- 不引入真实模型，使用确定性两阶段 graph 和同步屏障证明竞态，不依赖随机 sleep。

## 8. 评审出口

本次推荐先实施阶段一；阶段二按技术验证结果完成可靠 rollback 与 keep_latest。需要评审的是完整范围、临时不支持行为和必要数据库适配，不是再次确认普通读代码权限。

必须在进入完整实现前落实：固定官方行为表；真实 checkpoint/write 归属证据；数据库锁与原子写方案；是否需要 migration；历史依赖/DeltaChannel 的支持范围。未落实项保持待验证，不包装成已经确定可行的实现。
