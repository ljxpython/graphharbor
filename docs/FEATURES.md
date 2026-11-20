# GraphHarbor 功能现状总览 (FEATURES.md)

> **AI 读取与维护规则：**
> - 本文件是 GraphHarbor 的**语义记忆（Semantic Memory）**，记录全仓库功能与协议能力现状；
> - 每项任务新增或变更功能时，必须在 Task Completion Card 中勾选并同步更新本文件对应的一行；
> - 状态定义：`🟢 Supported`（已实现且有单测/E2E验收通过）、`🟡 Partial`（部分实现或有已知边缘限制）、`🔴 Unsupported`（暂不支持，显式返回 404/501）。

---

## 一、Core Agent Protocol API 面 (`libs/langhost`)

| 功能模块 | 子能力 / 端点 | 状态 | 代码位置 | 说明与关联文档 |
|---|---|---|---|---|
| **服务发现与元数据** | `GET /ok`、`GET /info`、`GET /openapi.json` | 🟢 Supported | `libs/langhost/src/langhost/server.py` | 兼容官方健康检查与元数据反射 |
| **Assistants API** | 创建、获取、更新、搜索 (`/assistants`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 支持 `langgraph.json` 自动导入助理 |
| **Threads API** | 线程创建、检索、更新、克隆 (`/threads`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 支持会话隔离与状态追溯 |
| **Thread TTL 与可靠删除** | `GET/PATCH /threads/{id}/retention`、`POST /threads/{id}/touch`、`GET /threads/{id}/deletion`、手动/自动统一删除 | 🟢 Supported | `libs/langhost/src/langhost/core_api.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/retention.py`、迁移014 | post48正式双包已发布；固定/闲置分钟TTL、显式活动、持久前后钩子/重试、完成墓碑metadata最小化；正式包13项及使用方三服务通过，见[专项](projects/20261030-thread-retention-lifecycle/README.md) |
| **Thread metadata CAS** | `PATCH /threads/{id}/metadata/cas`、普通PATCH原子merge | 🟡 Partial | `libs/langhost/src/langhost/core_api.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/thread_metadata.py` | 源码/PG/HTTP/冻结依赖候选/使用方真实模型已验；post44 产物已包含 CAS，使用方正式源接入仍由[专项](projects/20261009-thread-metadata-cas/README.md)验收 |
| **Runs API** | 运行创建、执行、等待、取消 (`/threads/{id}/runs`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | post42已发布/PyPI/Final通过；false=202、true持久停止、未确认503，SSE/join同边界；null body差异/混合风险保留，见 [专项](projects/20261007-worker-cancel-propagation/README.md) |
| **Run 接受回执/只读对账** | `POST/GET /threads/{id}/runs/acceptance`、固定key/raw摘要、持久拒绝与永久墓碑 | 🟢 Supported | `libs/langhost/src/langhost/run_acceptance.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_acceptance.py`、迁移013 | done、13/13；正式post45双包已发布，wheel/sdist各5组进程及使用方notify/Stop/撤权联合通过，B01解除；接受与停止/通知完成分开，见[专项](projects/20261010-run-acceptance-receipts/README.md) |
| **Thread 固定目标取消** | `POST /threads/{id}/runs/cancel-active`、固定取消回执分页GET | 🟡 Partial | `libs/langhost/src/langhost/cancellation.py`、迁移011 | 正式post44已包含通用原语/迁移011，原post43候选/HTTP/Docker/恢复证据保留；使用方正式源接入仍由[专项](projects/20261007-thread-cancellation/README.md)验收，不据本发布自动改状态 |
| **Threadless Runs** | 无线程单次运行 (`POST /runs/stream`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 临时线程自动创建与即时执行 |
| **Cron API** | SDK 创建、搜索/计数、读取、更新、暂停/删除、到期 Run (`/runs/crons`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/cron.py` | post41 已发布；锁定 SDK 的隔离库 E2E、DST、故障/重启、租户、有限耗尽与并发测试通过；见 [专项](projects/20261005-cron-parity/README.md) |
| **Run/Cron completion webhook** | 原生 webhook 配置、原子 terminal snapshot/outbox、HMAC 受管 delivery、lease/retry/dead-letter/replay | 🟡 Partial | `libs/langhost/src/langhost/core_api.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/webhooks.py`、`webhook_delivery.py` | post44发布/引擎回归/容量/回退完成，正式最短通过；native定时/回填部分证据已保存，正式完整矩阵资源blocked，见 [专项](projects/20261009-run-completion-webhooks/README.md) |
| **Store API** | 键值检索、搜索、命名空间 (`/store/items`) | 🟢 Supported | `libs/langhost/src/langhost/store_api.py` | 兼容官方 BaseStore 接口规范 |
| **Custom Routes & Auth** | 用户自定义 FastAPI 路由与中间件鉴权挂载 | 🟢 Supported | `libs/langhost/src/langhost/server.py` | 支持 `langgraph.json` 的 `http.app` 扩展 |

---

## 二、持久化与分布式调度引擎 (`libs/langgraph-runtime-pg`)

| 功能模块 | 核心能力 | 状态 | 代码位置 | 说明与关联文档 |
|---|---|---|---|---|
| **Checkpoint 状态存储** | PostgreSQL 事务级 Checkpoint 读写 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 事务一致性，防幂等写入与状态回滚 |
| **Checkpoint 存储增长治理** | 最小运行边界/首次before-image、反向投影、安全prune与blob GC | 已完成（done），post49正式发布 | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint_mutations.py`、`checkpoint_journal.py` | migration015单次转换；六组容量与正式源42项通过，不内置业务保留政策或承诺永久状态有界，见[验收与限制](projects/20261120-checkpoint-storage-governance/verification.md) |
| **分布式行级 Lease 租约** | PostgreSQL 乐观锁/行锁租约保护 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 节点并发抢占与租约心跳续期 |
| **崩溃自动收割 (Reaper)** | 节点故障超时自动释放与重新排队 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 杜绝 Worker 崩溃导致任务永久停滞 |
| **Worker 图级优雅停机** | superstep 边界 drain 与同 Run 续跑 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` | 隔离 PostgreSQL/Redis 契约与真实 checkpoint 续跑通过 |
| **Worker 取消传播与停止确认** | 控制队列即时通知、清理续租与持久停止确认 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py`、`redis_stream.py`、`run_store.py` | post42已发布/PyPI/Final通过，专项done；默认/9项子图、故障/kill/回退/性能安全通过；无迁移/业务字段，混合版本不提供新版保证，见 [专项](projects/20261007-worker-cancel-propagation/README.md) |
| **Worker attempt 时间预算** | 按官方每attempt硬限、模型error、DB checkpoint重试；正常handoff不占故障次数，租约代次递增，私有factory预算 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_store.py`、`production_worker.py` | done；正式post42/使用方锁定/冷安装、12组HTTP/SSE、新PID接管与匹配源码/post41回退通过；平台前端另验，现役未升级，见[专项](projects/20261006-run-timeout-budget/README.md) |
| **Redis 任务调度** | Redis List 队列与 Pub/Sub 事件总线 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 高性能流式事件广播与跨进程排队 |
| **Schema 数据库迁移** | Alembic 自动迁移命令 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/` | 执行 `graphharbor migrate upgrade` |
| **到期维护** | CLI 默认只读预览、Worker 每轮后等待 60 秒、有限批次/并发领取 | 🟢 Supported | `libs/langhost/src/langhost/cli.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/retention.py` | 默认100、上限1000；活跃Run/未确认Lease/取消及启用Cron保护；无独立TTL关闭开关，见[维护/回退](runbooks/production-runbook.md#thread-ttl-and-durable-deletion-post48) |
| **通用业务边界隔离** | 核心模型不混入特定模型名/业务trace | 🟡 Partial | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 详见 `20260925-runtime-business-boundary-decoupling` |

---

## 三、流式传输与网络鲁棒性 (`libs/langhost`)

| 功能模块 | 核心能力 | 状态 | 代码位置 | 说明与关联文档 |
|---|---|---|---|---|
| **Protocol v2 SSE 流** | `values` / `updates` / `messages` / `custom` | 🟢 Supported | `libs/langhost/src/langhost/streaming.py` | 官方标准事件序列化协议输出 |
| **流式保活心跳注入** | 网关定时注入 15s ping 保活帧 | 🟢 Supported | `libs/langhost/src/langhost/streaming.py` | 根治 Nginx/Ingress 45s 超时断流 |
| **断线无感续传** | 支持客户端 `Last-Event-ID` 重放 | 🟢 Supported | `libs/langhost/src/langhost/streaming.py` | 从断开点补全历史事件，消除重复执行 |
| **HITL 中断与审批** | 人机交互 `interrupt` / `resume` 状态机 | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 审批流暂停、状态更新与已决中断去重 |

---

## 四、多智能体深度可观测性 (`Multi-Agent Observability`)

| 功能模块 | 核心能力 | 状态 | 代码位置 | 说明与关联文档 |
|---|---|---|---|---|
| **子智能体命名空间隔离** | `checkpoint_ns` 路由参数放通 | 🟢 Supported | `libs/langhost/src/langhost/protocol_api.py` | 解决嵌套子智能体内部工具调用丢失 |
| **跨命名空间历史查询** | `POST /state/checkpoint` 定向快照查询 | 🟢 Supported | `libs/langhost/src/langhost/protocol_api.py` | 官方 SDK 同名端点对齐，支持子图回溯 |

---

## 五、生态与客户端兼容

| 生态工具 | 兼容范围 | 状态 | 验证方式 |
|---|---|---|---|
| **LangSmith Studio** | 图形化可视化调试、实时断点与状态编辑 | 🟢 Supported | 启动打印 Studio 连接 URL，浏览器直接打开调试 |
| **langgraph-sdk (Python)** | 官方 Python SDK 客户端全方法调用 | 🟢 Supported | E2E 自动化测试套件持续集成覆盖 |
| **langgraph-sdk (JS/TS)** | 官方 TypeScript / JavaScript SDK | 🟢 Supported | 遵循标准 Core REST/SSE 契约 |
| **Agent Chat UI** | 官方开源聊天前端面板 | 🟢 Supported | 默认提供连接参数即开即用 |
| **LangSmith 闭源企业功能** | 多租户云控、专有企业部署编排器 | 🔴 Unsupported | 显式排除，不伪造成功结果（见 `compatibility/exclusions.json`） |

流资源边界：post39 已发布；Redis 写入滑动 TTL 3600 秒、终态本地缓冲回收、Protocol 历史每页 32 条。见 [专项](projects/20261005-stream-resource-bounds/README.md)。
