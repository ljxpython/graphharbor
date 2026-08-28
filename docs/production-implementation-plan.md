# GraphHarbor 生产化实施规划

本文把 `production-decisions.md` 转换为阶段、任务、验证证据和进入/退出门禁。具体 Core/Extended/Unavailable 范围见 [`compatibility-profile.md`](compatibility-profile.md)。

## 0. 实施规则

### 0.1 先兼容性，后包装

先证明最新 LangGraph、完整目标 Agent Protocol、runtime、custom routes 和当前 graph 能一起工作，再推进发布包装。PyPI 不是兼容性验证。

阶段 A 的 compatibility spike 只允许内部使用；只有阶段 B 的完整 drop-in 兼容套件通过，才允许对外宣称生产兼容。

### 0.2 每阶段保留证据

每阶段至少记录输入版本、环境、修改、命令、测试结果、未覆盖边界和回滚方式。

### 0.3 代码实施前门禁

六项冻结已完成。下面内容进入每个切片时仍必须有契约和验收证据：

~~~text
无 License Key 的完整 drop-in server 边界
官方兼容 endpoint 全量清单
Principal/auth contract
HITL/cancel 状态语义
P0 graph 范围
数据迁移和容量目标
~~~

## 1. 阶段总览

~~~text
S0 决策与现状基线
  ↓
S1 最新依赖升级实验
  ↓
S2 runtime / PostgreSQL / Redis 兼容
  ↓
S3 lifespan + custom routes + auth
  ↓
S4 v2/v3 协议和事件投影
  ↓
S5 P0 graph 真实 E2E
  ↓
S6 多 worker / 故障 / 扩容
  ↓
S7 本地部署基线
  ↓
S8 TestPyPI / PyPI 预览发布
  ↓
S9 生产灰度与正式发布
~~~

## 2. S0：决策与现状基线

### 任务

1. 将已确认的六项冻结写入 baseline，并登记 owner/date。
2. 盘点 GraphHarbor 和 ai-agent-platform 的依赖及 lockfile。
3. 盘点 `runtime_service/langgraph.json` 的 graph、auth、custom app。
4. 盘点 custom routes、auth handler 和前端 SDK 调用。
5. 运行 GraphHarbor runtime 单测和当前项目测试。
6. 生成 `docs/compatibility-baseline.md`。

### 验证

~~~bash
uv lock --check
uv run python scripts/check_versions.py
uv run pytest
uv run pytest runtime_service/tests -q
uv run python -m compileall runtime_service
~~~

### 退出条件

- 有唯一版本基线。
- 有 custom route/auth/graph 清单。
- 每项能力标记已验证、未验证或不适用。
- 开放决策有 owner 和决定日期。

## 3. S1：最新依赖升级实验

### 任务

1. 查询最新稳定 LangGraph 依赖。
2. 为 GraphHarbor 和 ai-agent-platform 建立同一目标版本矩阵。
3. 在独立分支更新 `langgraph-api`、`langgraph-sdk`、`langgraph`、`langgraph-cli`。
4. 重新生成 lockfile。
5. 检查 runtime import、edition hook、lifespan 和 migration。
6. 检查 Python 版本支持范围和新旧 API 差异。

### 最小验证

~~~bash
uv lock
uv lock --check
uv run python scripts/check_versions.py
uv run pytest libs/langhost/tests/test_cli.py -q
uv run pytest libs/langgraph-runtime-pg/tests -q
~~~

服务 smoke 必须验证 `/ok`、`/info`、`/openapi.json`、graph discovery 和至少一个 thread/run/stream。

### 退出条件

- 最新目标依赖能安装。
- runtime migration 能执行。
- 一个 P0 graph 能真实运行。
- 内部 compatibility spike 的启动结论已明确。
- 已形成完整 Agent Protocol endpoint 清单，不能用“当前项目暂时没调用”缩减范围。
- 未通过完整 drop-in 兼容之前，不进行公开生产发布。

## 4. S2：runtime、PostgreSQL、Redis 兼容

### 任务

PostgreSQL：空库/重复 migration、threads/assistants/runs CRUD、checkpoint 恢复、exactly-once claim、heartbeat/reclaim、retry/dead-letter、backup/restore。

Redis：job queue、Pub/Sub、replay cursor、cancel channel、terminal signal、断线、共享 Redis、多实例和短暂不可用。

可靠性：同/不同 thread 并发、worker kill、PG/Redis 重启、API/worker 分离。

### 验证

~~~bash
uv run pytest libs/langgraph-runtime-pg/tests -q
uv run pytest runtime_service/tests -q
~~~

必须增加真实 HTTP/SDK 集成测试，不能只依赖 runtime fixture。

### 退出条件

- PostgreSQL 是状态事实源。
- Redis 在多实例下不串流。
- worker 崩溃后 run 按策略恢复。
- cancel 不覆盖 terminal 状态。
- 失败有可观测错误和恢复结果。

## 5. S3：lifespan、custom routes、auth

### 任务

1. 用最小 FastAPI lifespan probe 验证 startup/shutdown。
2. 验证 server/runtime/custom 三层启动顺序。
3. 采用 core lifespan 包裹 user lifespan 的组合方式。
4. 禁止同一 app 混用 startup/shutdown handler 和 lifespan。
5. 将 migration 从启动流程拆成独立命令。
6. 建立 Principal：subject、tenant、project、roles、scopes、credential_type。
7. 统一 Agent Server Auth 与 custom routes auth dependency。
8. 验证跨租户资源返回 404，客户端 configurable 不能覆盖认证身份。
9. 逐个验证所有 custom routes 的路径、响应、权限和数据边界。

### 验证

~~~text
启动成功 → readiness 200
custom route 200
/info 200
thread/run 200
未认证 401
无权限 403
跨租户资源 404
错误/过期 token 401
shutdown 逆序释放
~~~

### 退出条件

- 所有 custom routes 有清单和测试。
- 三层 lifespan 不互相覆盖。
- auth 只有一个可信身份来源。
- readiness 不早于 runtime/custom 资源就绪。

## 6. S4：v2/v3 协议与事件

### 任务

1. 保留 `client.runs.stream(..., version="v2")` 作为兼容基线，不改变调用代码。
2. 验证 `graph.stream(..., version="v2")` 和 `graph.stream_events(..., version="v3")`。
3. 验证远程线程级 event stream 和 commands，保持官方客户端调用方式。
4. 建立事件 envelope、seq、cursor、namespace、path、run_id、thread_id 规范。
5. 实现主图/子图 lifecycle。
6. 实现 interrupt/resume、values、updates、messages、custom、debug、events。
7. 验证 SSE heartbeat、断线、重连和 replay。
8. 增加协议 kill switch 和 capability probe。

### 验证矩阵

~~~text
普通消息流 / token 流 / 多模式流 / 工具 content blocks
主图 lifecycle / 子图 lifecycle
HITL interrupt / Command resume / 多次 interrupt
错误 / 取消 / 断线 cursor 恢复
~~~

### 退出条件

- P0 事件在真实 SDK 中可解析。
- 子图事件不要求前端猜 namespace 字符串。
- HITL resume 不重复执行。
- 重连不会无限等待或产生无法识别的重复事件。
- 协议异常可通过 kill switch 回退。
- 官方 Python/JavaScript SDK、Studio 和 Agent Chat UI 无业务适配即可通过 smoke。

## 7. S5：P0 graph E2E

P0：`assistant`、`test_case_agent_v2`、`customer_support_handoffs_demo`、`deepagent_demo`、`personal_assistant_demo`。

每个 graph 验证：

~~~text
graph discovery / assistant lookup / thread create / run create
client.runs.stream v2 / event stream v3 / state persistence / history
subgraph lifecycle / interrupt-resume / custom auth / error handling
~~~

退出条件：全部 P0 graph 通过真实 HTTP/SDK E2E，外部依赖失败策略已记录，单 graph 失败不会污染其他 thread/tenant。

## 8. S6：多 worker、故障和扩容

### 任务

1. API 与 worker 分开启动。
2. 运行 1/2/4 worker 和 2/4 API 实例。
3. 使用共享 PostgreSQL/Redis 观察任务分配。
4. kill worker，观察 lease/reaper/retry。
5. SIGTERM，观察 drain/requeue。
6. 断开客户端，观察 SSE replay。
7. 重启 Redis/PG，观察新 run、已有 run 和恢复。
8. 压测 queue depth、首事件延迟、完成延迟和错误率。

### 退出条件

- 实例无状态。
- 同一 run 不会被两个 worker 同时执行。
- worker 崩溃不丢 run。
- graceful shutdown 不把任务误记失败。
- SSE 可跨 API 实例继续接收事件。

## 9. S7：部署交付

### 本地部署

提供不依赖 Docker 的 API、worker、独立 migration 启动命令；本机 PostgreSQL/Redis 可直接作为验收依赖。

### 通用要求

- 数据库和 Redis 不与 API 共用不可独立恢复的生命周期。
- gateway 负责 TLS、CORS、限流和 body 限制。
- secrets 不进镜像和日志。
- 生产禁止 `allow_origins=["*"]`。

## 10. S8/S9：发布和迁移

### TestPyPI 前

~~~bash
uv lock --check
uv run python scripts/check_versions.py
uv run pytest
uv build --package graphharbor-runtime
uv build --package graphharbor
~~~

另需完成 P0 E2E、协议、故障和安全回归。

### 发布顺序

~~~text
TestPyPI
→ 隔离环境安装
→ 最小 graph + P0 smoke
→ 人工批准
→ PyPI preview/stable
→ GitHub Release
~~~

### 迁移顺序

~~~text
langgraph dev
→ compatibility mode
→ 本机 PG/Redis
→ 服务器进程管理
→ 灰度流量
→ 正式切换
~~~

## 11. 阶段产物

~~~text
docs/production-decisions.md
docs/production-implementation-plan.md
docs/compatibility-baseline.md
docs/compatibility-matrix.md
docs/protocol-v2-v3-contract.md
docs/hitl-contract.md
docs/runbooks/incident-recovery.md
~~~

## 12. 当前状态与下一步

公共协议、Principal/auth、run 状态机、PostgreSQL schema、Core 资源 API、v2/Protocol v2
流式切片、HITL 幂等和本地 OpenSpec 实施骨架已经落地，并通过当前 runtime 回归与官方 Python
SDK 契约测试。现在可以进入完整生产重写实施阶段，但这不等于已达到最终生产发布门禁：

1. 补齐官方 JavaScript SDK/REST 全量契约，并在真实网络传输下验证长连接重连。
2. 对当前 `runtime_service/langgraph.json` 的 P0 graphs 做真实 HTTP + 官方 SDK E2E，验证 custom routes、auth 和 lifespan；该外部源码不在当前工作区，acceptance fixture 通过不能替代此项。
3. 完成双 API/双 worker、PG/Redis/API 重启、跨实例 SSE replay 和观测信号验证。
4. 通过 Python 3.11/3.12/3.13、Python/JavaScript SDK、租户隔离和故障恢复门禁后，才进入 TestPyPI。
