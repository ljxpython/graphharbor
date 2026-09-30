# GraphHarbor 工业级系统架构与源码深潜中心

> 📌 **核心定位**：本目录为 GraphHarbor 系统核心架构与源码全景解密库，专为系统学习者、开源贡献者及**进行深度二次开发的架构师与工程师**量身打造。
> 
> 严守**切斯特顿栅栏（Chesterton's Fence）**、**双层渐进透析（Two-Tier Progressive Concept Disclosure）**与**极限防御推演**原则，拒绝空洞的宏观方块图与机械的代码搬运，所有结论直指真实源码路径、数据库物理 Schema、真实网络报文与生产防爆死线。

---

## 🗺️ 架构专题全景导航 (Architecture Modules)

系统按职责边界解耦为 6 大核心模块。二次开发者应遵循自顶向下、由粗到细的认知路径：

```text
               ┌────────────────────────────────────────────────────────┐
               │         01. 系统全貌、双包物理边界与启动生命周期         │
               │        (libs/langhost vs libs/langgraph-runtime-pg)     │
               └───────────────────────────┬────────────────────────────┘
                                           │
         ┌─────────────────────────────────┴─────────────────────────────────┐
         ▼                                                                   ▼
┌─────────────────────────────────┐                         ┌─────────────────────────────────┐
│ 02. ASGI 协议网关与鉴权守卫     │                         │ 04. Redis 队列与分布式调度执行核 │
│ (Starlette / Core REST / MCP)   │                         │ (Redis Stream / Lease / Reaper) │
└────────────────┬────────────────┘                         └────────────────┬────────────────┘
                 │                                                           │
                 │                ┌─────────────────────────┐                │
                 └───────────────►│ 03. PostgreSQL 状态持久化│◄───────────────┘
                                  │ (FencedSaver / Checkpoint)
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ 05. Graph 执行与流式事件 │
                                  │ (Registry / SSE Fanout) │
                                  └────────────┬────────────┘
                                               │
                                               ▼
                                  ┌─────────────────────────┐
                                  │ 06. 二次开发与工程实战  │
                                  │ (Custom Graph / Harness)│
                                  └─────────────────────────┘
```

| 模块编号与专题 | 职责范围与技术边界 | 核心落地源码坐标 | 主干文档入口 |
|---|---|---|---|
| **[01. 系统全貌与双包分层](01-overview-and-packaging/README.md)** | Monorepo 双包解耦、锁步发版契约、CLI 启动与 Lifespan 探针 | `libs/langhost/` / `libs/langgraph-runtime-pg/` | [01-overview-and-packaging/README.md](01-overview-and-packaging/README.md) |
| **[02. ASGI 协议网关与鉴权](02-gateway-and-protocol/README.md)** | Starlette 边界、Core REST 路由映射、Principal 隔离与 MCP 传输 | `libs/langhost/src/langhost/server.py` | [02-gateway-and-protocol/README.md](02-gateway-and-protocol/README.md) |
| **[03. PostgreSQL 状态机与栅栏](03-persistence-and-fencing/README.md)** | FencedPostgresSaver、防脑裂代际校验、增量快照与回滚基线 | `langgraph_runtime_pg/checkpoint.py` | [03-persistence-and-fencing/README.md](03-persistence-and-fencing/README.md) |
| **[04. Redis 任务队列与分布式租约](04-distributed-scheduler-and-worker/README.md)** | Redis Streams 消费、Wake Generation 单调递增、Lease 租约与 Reaper | `langgraph_runtime_pg/production_worker.py` | [04-distributed-scheduler-and-worker/README.md](04-distributed-scheduler-and-worker/README.md) |
| **[05. Graph 执行核与流式事件](05-execution-and-streaming/README.md)** | 动态 GraphRegistry、GraphExecutor 执行栈、SSE 扇出与 Last-Event-ID 重播 | `langhost/streaming.py` | [05-execution-and-streaming/README.md](05-execution-and-streaming/README.md) |
| **[06. 二次开发与工程实战](06-secondary-development-guide/README.md)** | 自定义 Graph 挂载、自定义 Checkpoint/Store 扩展、本地隔离测试防清库 | `tests/` / `langgraph.json` | [06-secondary-development-guide/README.md](06-secondary-development-guide/README.md) |

---

## 📚 全平台概念透析总字典 (Concept Glossary Index)

> 💡 **防文档熵增铁律（Single Source of Truth Catalog）**：
> 为避免概念专篇散落各处形成死链与知识孤岛，凡是在各子系统 `concepts/` 目录中新增的深度专篇，必须在此表完成**回执打卡**。严禁存在任何无索引的幽灵文档！

| 所属专题 / 模块 | 专篇文件与直达链接 | 核心解决的门槛认知与生产痛点 |
|---|---|---|
| **01-overview** | [01-Monorepo 双发布包锁步发版与边界隔离](01-overview-and-packaging/concepts/01-monorepo-dual-package-lockstep.md) | 扫除发包与依赖盲区：为什么拆成 `graphharbor` 与 `graphharbor-runtime`？为什么必须强制执行 100% 锁步版本？如何防范版本漂移灾难？ |
| **01-overview** | [02-通用 Agent Server 边界隔离与防业务侵蚀](01-overview-and-packaging/concepts/02-generic-agent-server-boundary.md) | 切斯特顿栅栏生产实战：为什么通用运行时绝对严禁写入业务表字段？解密 Migration 008 剔除 `tenant_id` 与 `project_id` 的深层技术考量。 |
| **02-gateway** | [01-官方协议黑盒适配：如何 100% 欺骗官方前端与客户端 SDK](02-gateway-and-protocol/concepts/01-langgraph-protocol-adaptation.md) | 官方协议黑盒解密：如何做到无需闭源 SDK 即可 100% 欺骗官方前端与客户端。 |
| **02-gateway** | [02-Token 校验、Principal 上下文与线程所有权隔离](02-gateway-and-protocol/concepts/02-principal-and-token-isolation.md) | Token 校验、Principal 上下文与线程所有权隔离（IDOR 越权攻击防护与 HMAC 签名委托）。 |
| **03-persistence** | [01-生产防裂脑：Fencing Token、Generation 代际递增与过期写拦截](03-persistence-and-fencing/concepts/01-fencing-token-and-split-brain.md) | 生产防裂脑：Fencing Token、Generation 代际递增与过期写拦截（`CheckpointConflict`）。彻底消除僵尸 Worker 假死恢复后的状态写穿灾难。 |
| **03-persistence** | [02-子智能体历史穿越：定向 checkpoint_ns 路由与嵌套调用轨迹持久化](03-persistence-and-fencing/concepts/02-subagent-checkpoint-namespace.md) | 子智能体历史穿越：定向 `checkpoint_ns` 路由与嵌套调用轨迹持久化。智能绕过 Pregel 静态拓扑解析崩溃，直通底层检查点引擎。 |
| **04-scheduler** | [01-高并发防假死：Wake Generation 单调递增与 Redis Pub/Sub 惊群抑制](04-distributed-scheduler-and-worker/concepts/01-redis-stream-wake-generation.md) | 高并发防假死：Wake Generation 单调递增与 Redis Pub/Sub 惊群抑制。消除传统 Event wait/clear 竞态丢唤醒挂起。 |
| **04-scheduler** | [02-租约生命周期：心跳续约、租约过期判定与自愈收割机极限推演](04-distributed-scheduler-and-worker/concepts/02-distributed-lease-and-reaper.md) | 租约生命周期：心跳续约、租约过期判定与自愈收割机（Reaper）极限推演。彻底解决 Worker 崩溃引发的永久 Running 任务死锁。 |
| **05-execution** | [01-复杂状态机：Human-in-the-loop（中断/审批/回滚/恢复）跃迁全景图](05-execution-and-streaming/concepts/01-run-state-machine-and-interrupt.md) | 复杂状态机：Human-in-the-loop（中断/审批/回滚/恢复）跃迁全景图。审批期间物理资源零驻留，指令唤醒现场复现。 |
| **05-execution** | [02-真实流式通信：SSE 事件扇出、Last-Event-ID 重播与保活心跳](05-execution-and-streaming/concepts/02-sse-event-fanout-and-resume.md) | 真实流式通信：SSE 事件扇出、Last-Event-ID 重播与保活心跳。彻底杜绝移动端网络抖动断流与反向代理 60s 超时掐线。 |
| **06-secondary-dev**| [01-测试隔离体系：无外部大模型依赖注入与破坏性清库防护](06-secondary-development-guide/concepts/01-testing-and-mock-harness.md) | 测试隔离体系：无外部真实大模型依赖注入（Fake 确定性编排）与破坏性清库防护（`scripts/test.sh` DROP/TRUNCATE 隔离门禁）。 |

---

## 🛡️ 二次开发三大红线原则

任何二次开发人员在修改代码或提交 PR 前，必须熟读并默念以下三条红线：

1. ❌ **严禁单包私自提升版本号**：`libs/langhost` 与 `libs/langgraph-runtime-pg` 必须保持 100% 锁步发版（`0.13.0.post37` 等）；
2. ❌ **严禁在核心包引入业务字段**：通用运行时模型、表结构、CLI 与事件协议只能承载通用 Agent Server 语义，业务数据一律通过 `metadata` 或 `config` JSONB 透传；
3. ❌ **严禁在共享或生产数据库运行破坏性脚本**：`scripts/test.sh` 包含清库语句（`DROP TABLE`），只能在本地完全隔离的容器数据库中执行！
