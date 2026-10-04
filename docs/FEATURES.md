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
| **Runs API** | 运行创建、执行、等待、取消 (`/threads/{id}/runs`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 任务排队、取消信号与终态持久化 |
| **Threadless Runs** | 无线程单次运行 (`POST /runs/stream`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 临时线程自动创建与即时执行 |
| **Cron API** | 定时任务创建、列表、删除 (`/threads/{id}/runs/crons`) | 🟢 Supported | `libs/langhost/src/langhost/core_api.py` | 支持周期性触发与定时清理 |
| **Store API** | 键值检索、搜索、命名空间 (`/store/items`) | 🟢 Supported | `libs/langhost/src/langhost/store_api.py` | 兼容官方 BaseStore 接口规范 |
| **Custom Routes & Auth** | 用户自定义 FastAPI 路由与中间件鉴权挂载 | 🟢 Supported | `libs/langhost/src/langhost/server.py` | 支持 `langgraph.json` 的 `http.app` 扩展 |

---

## 二、持久化与分布式调度引擎 (`libs/langgraph-runtime-pg`)

| 功能模块 | 核心能力 | 状态 | 代码位置 | 说明与关联文档 |
|---|---|---|---|---|
| **Checkpoint 状态存储** | PostgreSQL 事务级 Checkpoint 读写 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 事务一致性，防幂等写入与状态回滚 |
| **分布式行级 Lease 租约** | PostgreSQL 乐观锁/行锁租约保护 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 节点并发抢占与租约心跳续期 |
| **崩溃自动收割 (Reaper)** | 节点故障超时自动释放与重新排队 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 杜绝 Worker 崩溃导致任务永久停滞 |
| **Worker 图级优雅停机** | superstep 边界 drain 与同 Run 续跑 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py` | 隔离 PostgreSQL/Redis 契约与真实 checkpoint 续跑通过 |
| **Redis 任务调度** | Redis List 队列与 Pub/Sub 事件总线 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/` | 高性能流式事件广播与跨进程排队 |
| **Schema 数据库迁移** | Alembic 自动迁移命令 | 🟢 Supported | `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/` | 执行 `graphharbor migrate upgrade` |
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
