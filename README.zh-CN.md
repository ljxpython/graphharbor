<h1 align="center">GraphHarbor</h1>

<p align="center">
  <strong>企业级开源 LangGraph Agent Server 基础设施</strong><br/>
  PostgreSQL 强持久化状态机 + Redis 高性能分布式 Worker · 兼容官方 SDK · 兼容 Studio · 零代码侵入
</p>

<p align="center">
  <strong>简体中文</strong> · <a href="README.md"><strong>English</strong></a>
</p>

<p align="center">
  <a href="https://github.com/ljxpython/graphharbor/stargazers"><img src="https://img.shields.io/github/stars/ljxpython/graphharbor?style=social" alt="GitHub stars"></a>
  &nbsp;
  <a href="https://github.com/ljxpython/graphharbor/actions/workflows/ci.yml"><img src="https://github.com/ljxpython/graphharbor/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  &nbsp;
  <a href="https://pypi.org/project/graphharbor/"><img src="https://img.shields.io/pypi/v/graphharbor" alt="PyPI"></a>
  &nbsp;
  <a href="https://pypi.org/project/graphharbor-runtime/"><img src="https://img.shields.io/pypi/v/graphharbor-runtime?label=graphharbor-runtime" alt="runtime PyPI"></a>
  &nbsp;
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-≥3.11-blue" alt="Python"></a>
  &nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
  &nbsp;
  <a href="https://opensource.org/licenses/MIT"><img src="https://img.shields.io/badge/100%25-open%20source-brightgreen" alt="100% open source"></a>
</p>

<p align="center">
  <em>如果 GraphHarbor 为你的智能体基础设施提供了支撑，欢迎 <a href="https://github.com/ljxpython/graphharbor">⭐ Star 本仓库</a> 支持纯粹开源的力量！</em>
</p>

---

## 什么是 GraphHarbor？

**GraphHarbor** 是一套专为高并发、高弹性生产环境打造的**企业级开源 LangGraph Agent Server**。

底层基于 PostgreSQL 事务级 Checkpoint 状态存储与 Redis 分布式任务队列，原生提供**故障崩溃自动自愈、子智能体执行轨迹全量持久化（`checkpoint_ns`）、生产级 SSE 流式长连接保活与断线续传，以及绝对纯净的通用底座边界** —— 与此同时，对官方 LangGraph 生态保持 100% 协议与 API 兼容。

全面兼容：
**[LangSmith Studio](https://docs.langchain.com/langsmith/studio)** · **[langgraph-sdk](https://pypi.org/project/langgraph-sdk/)** · **[Agent Protocol](https://docs.langchain.com/langsmith/server-api-ref)** · **[Agent Chat UI](https://github.com/langchain-ai/agent-chat-ui)**

---

## 为什么生产环境选择 GraphHarbor？

官方 `langgraph dev` 为本地开发带来了极佳的体验，但在面对生产落地时，往往受制于闭源运行时许可、内存单进程架构与多智能体轨迹丢失等问题。GraphHarbor 专为破除这些生产瓶颈而生：

| 能力对比 | [`langgraph dev`](https://docs.langchain.com/oss/python/langgraph/local-server) | [LangSmith Deployments](https://docs.langchain.com/langsmith/deployment) | [Aegra](https://github.com/aegra/aegra) | **GraphHarbor** |
|:---|:---|:---|:---|:---|
| **核心定位** | 本地快速原型迭代 | 商业托管 / 授权私有化 | FastAPI 开源平替 | **企业级自托管生产运行时** |
| **持久化引擎** | 内存 + 本地 SQLite | Postgres + Redis（闭源协议） | Postgres + Redis | **Postgres + Redis（MIT 开源引擎）** |
| **子智能体深度回溯** | 基础运行树（容易丢内部工具） | 依赖云端 LangSmith 控制台 | 基础支持 | **原生 `checkpoint_ns` 路由持久化与回放** |
| **Worker 容错自愈** | 单进程（无容错） | 专有编排服务 | 进程级管理 | **PostgreSQL 行级租约 + Redis 自动 Reaper** |
| **流式传输鲁棒性** | 本地流式 | 专有流式机制 | 基础 SSE 流 | **网关 15s 保活心跳 + `Last-Event-ID` 精准续传** |
| **核心协议支持** | 官方完整协议 | 官方完整协议 | 基础 Agent Protocol | **完整 Core 协议（assistants, threads, runs, crons, HITL）** |
| **Studio & SDK 兼容** | 是 | 是 | 是 | **是（零代码侵入，即插即用）** |
| **开源许可证 / 授权码** | Elastic-2.0 / 无需 Key | 商业闭源 / 强制要求 Key | Apache-2.0 / 无需 Key | **MIT（100% 商业友好）/ 无需 Key** |

---

## 🚀 五大企业级生产特性

- 🔍 **子智能体命名空间轨迹全回溯 (`checkpoint_ns`)**：彻底攻克官方与开源生态中嵌套智能体（Hierarchical Agents）内部工具调用丢失的顽疾，完整放通 `checkpoint_ns` 路由与 `/state/checkpoint` 端点，多智能体层级运行轨迹 100% 可审计、可回放。
- ⚡ **分布式行级租约与自动收割（Distributed Lease & Reaper）**：多 Worker 并发消费采用 PostgreSQL 事务级行锁 Lease 保护。任意节点异常崩溃或宕机，Lease 自动超时失效，Reaper 自动将其无缝重新入队二次分发，绝不污染持久化状态。
- 🌊 **生产级 SSE 容错心跳与断线续传**：网关内置 15 秒定时心跳帧，彻底解决生产反向代理（Nginx / K8s Ingress）45 秒超时断流问题；结合精确的 `Last-Event-ID` 重放机制，网络抖动断线后客户端无感自动补全丢失事件。
- 🛡️ **严格的通用 Agent Server 纯净边界**：严守基础设施边界红线，绝不私塞特定大模型供应商、专有提示词或定制追踪协议。GraphHarbor 专注做好通用运行时，业务策略 100% 留在使用方的智能体图中。
- 🔌 **LangGraph 生态无缝对接**：无需改写既有 `langgraph.json` 与业务图代码。直接兼容 LangSmith Studio、LangGraph 官方 Python / JS SDK 以及社区 Web 界面。

---

## 🏗️ 架构与双包分工

GraphHarbor 采用清晰的双包协同架构，各司其职且版本严格锁步：

```text
       Studio / langgraph-sdk / Agent Chat UI
                         │
                         ▼
┌──────────────────────────────────────────────────┐
│ libs/langhost (CLI: graphharbor serve)           │
│ - ASGI 协议网关（HTTP / SSE / 定时任务调度）     │
│ - 请求鉴权、租户隔离与路由分发                  │
│ - 保活心跳自动注入与 Last-Event-ID 断线续传      │
└────────────────────────┬─────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────┐
│ libs/langgraph-runtime-pg (graphharbor-runtime)  │
│ - PostgreSQL 事务级 Checkpoint 状态机            │
│ - 分布式行级 Lease 租约与 Reaper 崩溃收割自愈    │
│ - Redis 任务消费队列与 Pub/Sub 流式广播          │
└────────────────────────┬─────────────────────────┘
                         │
            ┌────────────┴────────────┐
            ▼                         ▼
   PostgreSQL（状态/运行记录）     Redis（队列/发布订阅）
```

- **`graphharbor` (`libs/langhost/`)**：对外暴露的 CLI 工具与 ASGI 网关服务（执行 `graphharbor serve`）。
- **`graphharbor-runtime` (`libs/langgraph-runtime-pg/`)**：负责核心持久化、Lease 锁抢占与并发调度的工业级运行时引擎。

---

## ⚡ 3 分钟快速起步

### 1. 准备或创建 LangGraph 应用

```bash
# 使用既有项目，或用官方工具初始化新智能体：
uvx --from langgraph-cli@latest langgraph new my-agent
cd my-agent
uv sync
```

### 2. 安装 GraphHarbor

```bash
uv add graphharbor
```

*(会自动引入对应锁步版本的 `graphharbor-runtime` 核心引擎)*

### 3. 配置数据库与 Redis 连接

在项目根目录下创建或编辑 `.env`：

```bash
DATABASE_URI=postgresql+asyncpg://postgres:postgres@localhost:5432/langgraph?sslmode=disable
REDIS_URI=redis://localhost:6379/0
```

### 4. 执行数据表迁移并启动服务

```bash
# 启动前执行一次数据库表结构迁移
uv run graphharbor migrate upgrade

# 开发环境运行（支持代码热重载）
uv run graphharbor serve --reload

# 生产环境运行（多 Worker 高并发模式）
uv run graphharbor serve --host 0.0.0.0 --port 31296 --workers 4
```

服务默认运行在 **31296** 端口，控制台将输出可用地址：
- **REST API:** `http://127.0.0.1:31296`
- **LangSmith Studio 调试:** `https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:31296`
- **Swagger 接口文档:** `http://127.0.0.1:31296/docs`

### 5. 使用官方 SDK 丝滑调用

```python
import asyncio
from langgraph_sdk import get_client

client = get_client(url="http://127.0.0.1:31296")

async def main():
    # 调用方式与官方 Agent Server 完全一致
    async for chunk in client.runs.stream(
        None,  # 自动创建临时线程
        "agent",  # langgraph.json 中定义的 assistant 名称
        input={"messages": [{"role": "human", "content": "你好，GraphHarbor！"}]},
    ):
        print(chunk.event, chunk.data)

asyncio.run(main())
```

---

## 📚 文档导航中心

GraphHarbor 建立了完善的分层文档治理体系，所有深度资料详见 [`docs/`](docs/)：

- 🧭 **[文档导航中心 (`docs/README.md`)](docs/README.md)** — 全景指南，包含系统架构、生产运维手册与开发者指引。
- 📋 **[官方协议兼容矩阵 (`docs/compatibility/profile.md`)](docs/compatibility/profile.md)** — 详细对比官方 LangGraph Server 的能力落地与边界现状。
- 🚦 **[发版规范与门禁准则 (`docs/standards/release-process.md`)](docs/standards/release-process.md)** — 双包锁步发版与 TestPyPI 验证门禁。
- 🛠 **[开发与测试指南 (`docs/guides/README.md`)](docs/guides/README.md)** — 本地开发、环境搭建与回归测试执行规约。
- 🚑 **[事故恢复与排障手册 (`docs/runbooks/incident-recovery.md`)](docs/runbooks/incident-recovery.md)** — 生产故障应对、Postgres/Redis 灾难恢复与包版本回滚。
- 📜 **[版本变更日志 (`docs/CHANGELOG.md`)](docs/CHANGELOG.md)** — 追踪历次版本演进与关键功能更新。

---

## 📁 仓库真实结构

```text
libs/
├── langhost/                  # graphharbor：CLI 命令行工具与 ASGI 网关
└── langgraph-runtime-pg/      # graphharbor-runtime：PostgreSQL + Redis 核心运行时
docs/                          # 规范化文档中心、兼容性测试库与运维手册
scripts/                       # 本地 CI 门禁与自动化测试脚本
tests/                         # 端到端全量验收测试套件
```

---

## 开源传承与许可证

本项目基于 [MIT 许可证](LICENSE) 开源。

GraphHarbor 起步于社区对自托管 LangGraph 运行时的独立探索与分支实践。如今，项目已全面蜕变并确立了完全独立的自主演进路线，在分布式高可用、故障自动愈合、多智能体深度轨迹审计与通用业务边界等深水区形成了自主的核心架构。

本项目与 LangChain, Inc. 无商业隶属关系。LangGraph 与 LangSmith 为 LangChain, Inc. 的注册商标。
