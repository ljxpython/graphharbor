# GraphHarbor AI 协作规范 (AGENTS.md)

这是项目的 AI 协作入口与行为约束层（Constraint Layer），定义了开发流程、双包路由、分级标准与验证要求。

---

## 🚀 会话初始化

**开始处理任何需求前，AI 必须主动读取 [docs/CONTEXT.md](docs/CONTEXT.md) 同步项目现状。**

- [docs/CONTEXT.md](docs/CONTEXT.md) 是会话状态快照，记录当前双包锁步版本、各包运行状态、活跃项目及近期关键决策。
- 改动完成后，AI 负责同步更新 `CONTEXT.md` 的“最后更新”与受影响的包状态行。
- `CONTEXT.md` **只保留当前有效事实，严禁堆砌历史**；历史沉淀在 `docs/projects/` 和 `docs/CHANGELOG.md` 中。
- 改动涉及具体包时，在同步 `CONTEXT.md` 之后，按下方「双包感知路由」读取对应规范。

---

## 🧠 经验库读取规则

**开始处理具体包或系统改动前，按需读取 [docs/lessons/](docs/lessons/) 下对应的经验文件（文件存在时）：**

- [docs/lessons/index.md](docs/lessons/index.md) — 快速索引，不确定时先看这里
- [docs/lessons/langhost-gateway.md](docs/lessons/langhost-gateway.md) — ASGI 协议网关、SSE 保活与路由踩坑经验
- [docs/lessons/runtime-persistence.md](docs/lessons/runtime-persistence.md) — PostgreSQL 状态机、分布式 Lease 与 Redis Worker 踩坑经验
- [docs/lessons/ai-workflow.md](docs/lessons/ai-workflow.md) — 破坏性测试、双包版本锁步与 AI 工作流踩坑经验

**无需全部全量加载**，只读取与当前任务直接相关的经验文件。

---

## 🧭 双包感知路由 (Package-Aware Routing)

本仓库为 Monorepo 双发布包架构，AI 在改动前必须明确所属包边界与开发范式：

| 组件 / 包 | 源码目录 | 职责与技术边界 | 规范入口 |
|---|---|---|---|
| **`graphharbor`** (CLI/网关) | `libs/langhost/` | 承载 CLI (`graphharbor serve`) 与 ASGI HTTP/SSE 网关；负责 Core REST/SSE 协议适配、鉴权分发与保活心跳 | [docs/compatibility/profile.md](docs/compatibility/profile.md)<br/>[docs/standards/](docs/standards/) |
| **`graphharbor-runtime`** | `libs/langgraph-runtime-pg/` | 负责 PostgreSQL 状态机（Checkpoints/Lease/Reaper）与 Redis 分布式任务调度；纯通用运行时底座 | `libs/langgraph-runtime-pg/tests/`<br/>[docs/standards/](docs/standards/) |
| **通用发版与集成** | 根目录 / `scripts/` | 双包锁步发版与端到端自动化测试 | [docs/standards/release-process.md](docs/standards/release-process.md)<br/>[docs/guides/README.md](docs/guides/README.md) |

**产品边界红线：**
GraphHarbor 是通用 LangGraph Agent Server。核心包、CLI、API、事件协议和数据库模型**只能承载通用运行时概念**；严禁加入特定业务的模型供应商、模型名、特定工具名、业务策略或 trace 字段。业务配置留在使用方的 graph factory、验收图或上层服务。

---

## 🚦 改动分级标准

改动分级由 AI 接到需求时自行按「影响范围」判定，不按代码量判定，用户无需手动调用命令：

### 1. 讨论类（Discussion）
- **范围：** 纯方案探讨、可行性分析、代码阅读、架构咨询。
- **动作：** 直接对话给出专业结论，不生成项目文档；讨论达成关键架构结论时，主动提议记录为 ADR 或落入对应规划文档。

### 2. 局部改动（Local Change）
- **范围：** 单包内部逻辑修复、内部重构、测试补充、加日志；不改变对外 CLI/REST/SDK/SSE 契约，不改变持久化 Schema 或并发调度边界。
- **动作：** 直接实现 → 运行针对性单元测试 → 交付。不强制创建 `docs/projects/` 文档。若改变了服务对外呈现的能力，在 [docs/CHANGELOG.md](docs/CHANGELOG.md) 的 `[Unreleased]` 追加一行。

### 3. 链路改动（Cross-boundary Change）
- **范围：** 跨双包调用、改变公开 CLI/REST/SDK/SSE 契约、改变事件时序与终态、修改数据库 Schema。
- **流程（AI 自动调用对应 Skill）：**
  ```text
  plan-project → implement-feature (生成任务完成卡) → 针对性链路测试 → verify-change
  ```
- **文档：** 在 `docs/projects/{YYYYMMDD}-{project-name}/` 创建项目文档（标准模板或多专题模板）。

### 4. 治理改动（Governance Change）
- **范围：** 架构重构、安全性/鉴权改造、生产恢复、数据迁移、包版本发布流程。
- **流程：**
  ```text
  plan-project → 方案评审（人工批准） → 分阶段实施 (implement-feature) → 全面验证 (verify-change)
  ```
- **硬约束：** AI 绝不能自己批准治理方案，必须由人类明确批准后方可进入代码实施。

---

## 📜 跨包契约与置信度规则

跨包协议契约（REST/SSE 协议、发版门禁等）集中管理在 [docs/standards/](docs/standards/) 目录：

**置信度规则（标准文件头部含 `last_verified` 元数据）：**
- 🟢 `< 60 天` (`confidence: high`) → 可直接作为权威依据参考
- 🟡 `60–180 天` (`confidence: medium`) → 参考，但代码可能已超前，需核对最新代码
- 🔴 `> 180 天` (`confidence: low`) → 视为过期，不作强约束，以当前运行代码为准

**代码与文档冲突优先级规则：**
- **代码** = 当前运行的事实源；`docs/standards/` = 已批准的设计意图。
- 发现冲突时：以代码为当前基准，明确向用户指出差异，由人类裁决是“修代码”还是“更新文档”。
- 涉及**持久化破坏、安全鉴权或对外破坏性变更**的冲突：AI 必须停止并等待人工确认，不得凭直觉自行裁决。

---

## 🔄 项目收尾与自我进化机制

链路改动或治理改动完成前，AI 必须主动执行以下两项自我进化动作：

1. **经验提案（Lessons Learned Proposal）**：
   - 回顾本次项目是否遇到了值得沉淀的踩坑、边界陷阱或工具误用。若有，主动提炼为 **≤ 4 行**的极简经验条目，提议写入 [docs/lessons/](docs/lessons/) 对应模块，经用户确认后沉淀。
2. **标准文件毕业（Standard Promotion）**：
   - 若本项目对应 `docs/standards/` 中的某个草案（`draft`），且项目状态达到 `done`，AI 自动将该规范文件的 `status` 从 `draft` 改为 `active`，刷新 `last_verified` 日期，并同步更新健康表。
3. **进化边界（Evolution Boundary）**：
   - AI 可以更新工作记忆层（`CONTEXT.md`、标准文件、经验库），**严禁自主修改 `AGENTS.md` 和 Skills 规则本身**——约束层的变更必须由人类显式审批。

---

## 🎯 真实模型验收环境

需要真实模型端到端验收时，从本机 `~/.my_best/.env` 读取凭据（使用 `miaomiaoai` 的 `deepseek-v4.1-flash`）。
- 凭据仅注入验收临时进程，**严禁**复制或提交进仓库源码、文档、测试输出或日志；
- 优先使用确定性 Mock/Fixture 验证功能，真实模型仅用于最终端到端冒烟验证。

---

## ⛔ 普适性严禁行为清单 (已踩坑)

> 遵守以下硬性禁令（不合法状态），违者视为严重事故：

- ❌ **不得在未建 `tasks.md` 的情况下开始编写跨包或契约改动的代码**（链路与治理改动必须先规划）。
- ❌ **不得在非独立隔离的测试库中执行 `scripts/test.sh`**（该脚本包含 `drop table` 破坏性清库操作）。
- ❌ **不得向核心运行时引入特定业务的模型供应商、模型名、业务策略或 trace 字段**（严守通用 Agent Server 纯净边界）。
- ❌ **不得单包私自提升版本号**（`graphharbor` 与 `graphharbor-runtime` 必须严格保持锁步发版）。
- ❌ **不得把阶段性（Phase）验证记录写进最终（Final）验证**（两者必须分权独立）。
- ❌ **不得自己批准治理改动方案**（评审批准必须由人类明确给出）。
- ❌ **不得编造测试结果**（验证记录必须包含真实命令、实际产物或脱敏证据，未运行如实标明）。
- ❌ **不得用 `implementation/` 判定项目进度**（进度与完成度唯一事实源是 `tasks.md`）。
- ❌ **不得在用户未明确要求的情况下自动执行 git commit、push 或打 tag**。
