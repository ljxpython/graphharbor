# 01-Monorepo 双发布包锁步发版与边界隔离

> **概念定位与核心价值**：本专篇深度解密 GraphHarbor 为何坚决采用 `graphharbor` (CLI/网关) 与 `graphharbor-runtime` (持久化状态核) 的**双发布包 Monorepo 架构**，并以铁腕手段推行**版本锁步（Lockstep Packaging）**机制，彻底解决分布式微服务与 SDK 演进中最致命的“版本漂移（Version Drift）”与“依赖地狱（Dependency Hell）”。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
隔壁老李的理发店要换一套高档多功能理发椅：
- 底座（`graphharbor-runtime`）是液压升降泵、核心轴承和电机，埋在地砖里，负责沉重的动力支撑；
- 椅子上半身（`graphharbor` CLI/网关）是皮椅靠背、头枕和剪发工具架，给顾客提供操作和舒适界面。

如果椅背厂商按自己节奏发了“第 5 代极客版”，螺栓孔距改了 2 毫米，而底座电机还是“第 3 代标准版”。老李一通暴力安装硬拧上去，顾客一躺下，“咔嚓”一声螺栓当场崩断，顾客后脑勺直接磕地砖上，老李赔得当裤子！

要想彻底杜绝这种人间惨剧，唯一的办法就是：**底座和椅背必须同厂、同批次、同型号成套交付（锁步）！出厂打上完全相同的防伪序列号，任何一个零件不配套，安装工当场拒装！**

### 2. 软件工程演进痛点
在自托管 AI Agent 运行时的演进过程中，很多团队踩过血泪大坑：
1. **单包泥潭（Monolithic Package）**：所有东西打成一个包。某些只想把底层持久化状态机（Checkpoint + Lease）嵌入自己内网微服务的大厂用户，被迫把一堆 CLI 工具、美化字符图形库（`pyfiglet`）、HTTP 路由甚至 Uvicorn 通通打包进去，造成极度臃肿与依赖冲突；
2. **独立发版的版本漂移（Independent Version Drift）**：网关包发到了 `1.4.0`，底层运行时包发到了 `2.1.0`。两者在序列化协议、Redis 任务队列的消息 Header 格式、或者 Checkpoint 的 JSON 编码规范上发生了细微漂移。本地测试一切正常，一上生产由于用户版本排列组合，线上莫名其妙冒出不可复现的 `KeyError` 或反序列化异常。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：独立松散版本依赖，允许跨版本安装
# apps/cli/pyproject.toml
[project]
name = "naive-agent-cli"
version = "1.4.0"
dependencies = [
    # 致命伤：使用宽泛的兼容范围，用户环境可能安装 2.0.0 与 2.9.9 之间的任意版本
    # 一旦底层修改了 Checkpoint 的内部数据布局，网关毫不知情，直接裂脑崩盘！
    "naive-agent-runtime>=2.0.0,<3.0.0",
]

# ✅ 生产级落地方案 (GraphHarbor Production)：强硬锁步发版与 Monorepo 工作区约束
# libs/langhost/pyproject.toml
[project]
name = "graphharbor"
version = "0.13.0.post37"
dependencies = [
    # 铁律：版本号完全相同，由自动化发版流水线统一推进，杜绝任何中间状态
    "graphharbor-runtime==0.13.0.post37",
]

[tool.uv.sources]
graphharbor-runtime = { workspace = true }  # 本地开发强制直连单体源码树
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 核心拓扑与源码精准定位
GraphHarbor 在代码树的物理组织上实施了严格的包边界切分：

```text
/
├── pyproject.toml                         <-- Monorepo 根工作区 (tool.uv.workspace = ["libs/*"])
├── libs/
│   ├── langhost/                          <-- 包 1: graphharbor (CLI 与 ASGI 协议网关)
│   │   ├── pyproject.toml                 <-- 依赖 graphharbor-runtime==0.13.0.post37
│   │   └── src/langhost/                  <-- CLI, Core REST API, SSE Streaming, MCP
│   └── langgraph-runtime-pg/              <-- 包 2: graphharbor-runtime (通用持久化与调度核)
│       ├── pyproject.toml                 <-- 声明自身版本 0.13.0.post37
│       └── src/langgraph_runtime_pg/      <-- Postgres FencedSaver, Redis Queue, Worker
```

### 2. Monorepo 依赖冻结与解析屏障
查阅根目录 [pyproject.toml](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/pyproject.toml) 可见：

```toml
[tool.uv.workspace]
members = ["libs/*"]

[tool.uv.sources]
graphharbor-runtime = { workspace = true }
graphharbor = { workspace = true }

[tool.uv]
constraint-dependencies = [
    "langgraph-api==0.13.0",
    "langgraph-runtime-inmem==0.33.0",
    "langgraph-sdk==0.4.3",
    "langgraph==1.2.11",
    "langgraph-checkpoint==4.2.0",
    "langgraph-checkpoint-postgres==3.1.2",
]
package = false
```

- **开发态（Workspace Resolution）**：在本地开发与测试时，`tool.uv.sources` 保证两包互联互通，直接引用源码，修改任意一处即刻生效；
- **发布态（Lockstep Pinning）**：在对外打包为 Wheel 上传 PyPI 时，`libs/langhost/pyproject.toml` 的依赖声明会被锁定为确切的同版本（`graphharbor-runtime==0.13.0.post37`）；
- **上游生态锁定（Constraint Dependencies）**：锁死所有上游核心组件版本，防止因为 `langgraph` 或 `langgraph-checkpoint` 的隐式上游发布导致底座崩溃。

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：既然两个包版本完全锁死，为什么不干脆合二为一？
> **老王怒喷**：“合二为一？你想让别人的微服务把整个 CLI 和 Uvicorn 都背在身上吗？你知不知道什么叫关注点分离？！”
> 
> 1. **架构复用性**：有些二次开发团队需要在他们自己的内部框架（比如基于 Celery、Temporal 或裸 asyncio 进程池）中运行 LangGraph 状态持久化与调度逻辑。他们只需要引用 `graphharbor-runtime`，不需要 `langhost` 里的 CLI、Starlette 和 HTTP 路由！
> 2. **协议演进隔离**：`langhost` 的职责是 **100% 模拟与对齐 LangGraph 官方 Server 的 REST / SSE 协议**；而 `langgraph-runtime-pg` 的职责是 **PostgreSQL 和 Redis 的工业级高可用持久化**。前者面对外部网络客户端，后者面对内网基础设施。把它们物理隔离，才能保证底座的绝对纯净。

### 2. 工业级避坑清单
- ⚠️ **避坑 1：严禁私自为单个子包 bump 版本**
  在提交 PR 或发版时，绝不允许只改 `libs/langhost` 或只改 `libs/langgraph-runtime-pg`。两者的 `pyproject.toml` 中的 `version` 字段，以及 `libs/langhost` 依赖列表里的 runtime 版本号，**必须在同一个 Commit 中原子递增**！
- ⚠️ **避坑 2：严禁在 `langgraph-runtime-pg` 中反向导入 `langhost`**
  底座绝对不允许依赖上层网关！`langgraph-runtime-pg` 的任何源码文件如果出现了 `import langhost`，直接视为架构腐烂事故，CI 的 Lint 规则与架构门禁必须当场击毙！
- ⚠️ **避坑 3：严禁在非隔离测试库跑全套单测**
  根目录下 `scripts/test.sh` 包含全局测试编排，其中涉及破坏性清库操作（`DROP TABLE`）。二次开发跑测试必须使用专用 Docker 容器数据库，严禁指向包含业务数据的公共开发库！

---

## 四、架构不变量清单（Architectural Invariants）

1. **版本强锁步原则（Strict Lockstep Invariant）**：
   `graphharbor` 与 `graphharbor-runtime` 的发布版本号必须严格保持全等，`graphharbor` 必须以 `==` 强锁定对应版本的 `graphharbor-runtime`。
2. **依赖单向流动原则（Unidirectional Dependency Invariant）**：
   依赖方向只能是 `langhost` 依赖 `langgraph-runtime-pg`，绝不允许发生循环依赖或反向依赖。
3. **上游基线冻结原则（Upstream Pinning Invariant）**：
   所有与 LangGraph 官方相关的核心协议库与 Checkpoint 依赖必须在根工作区通过 `constraint-dependencies` 显式锁死，严禁使用通配符或未限定范围的依赖浮动。
