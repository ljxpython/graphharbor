---
name: implement-feature
description: AI 在项目文档（docs/projects/{YYYYMMDD}-{项目名}/）已存在时，实现任务过程中自动调用（不需要用户手动触发），输出 Task Completion Card 并记录改动细节到 implementation/ 目录。跳过条件：局部小改动无需使用。
---

# Implement Feature（功能与任务实现）

用于实施 GraphHarbor 已规划的链路或治理任务，并严谨记录改动信息与验证证据。**这是 AI 在链路/治理改动实现过程中自动调用的 Skill**，严格依附于 `plan-project` 创建的项目文档。

## 触发条件

- 已有项目文档（`docs/projects/{YYYYMMDD}-{项目名}/`）
- 准备开始实现某个已规划的 Task
- 跨双包（`langhost` / `langgraph-runtime-pg`）、公开 REST/SSE 契约或持久化 Schema 变动

## 跳过条件

- 局部小改动（单包内部 bug 修复、加日志、内部重构），直接实现并运行针对性单测，不创建项目任务记录

---

## 进度层与细节层分工原则

| 分层 | 核心载体 | 核心职责 | 读者视角 |
|---|---|---|---|
| **进度层** | `tasks.md` / 子专题文档的「任务拆分」 | 做没做完、预期结果、真实验证证据、合规 Checklist | **全局进度的唯一事实源**，任何人看进度只看这里 |
| **细节层** | `implementation/` | 详细代码前后对比、迁移脚本、复杂排障细节 | 细节档案库，仅在深挖技术实现时查阅，**严禁作为进度判定依据** |

---

## 工作流 (SOP)

### 1. 读取任务定义
- **标准模板**：读取 `docs/projects/{YYYYMMDD}-{项目名}/tasks.md`
- **多专题模板**：读取对应子专题文档 `docs/projects/{YYYYMMDD}-{项目名}/{序号}-{子专题}.md` 里的任务清单
- 确认当前任务的四要素：改动内容、代码位置、预期结果、验证项。若原任务定义模糊，先补齐预期结果与验证命令，再写代码。

### 2. 编写代码与针对性验证 (Phase Verification)
- 遵循 KISS / DRY / SOLID 原则实现代码。
- 严禁越过通用 Agent Server 边界（不得将模型名、工具名或业务专有字段渗入 `langgraph-runtime-pg` 核心模型）。
- 运行针对该任务的**最小相关验证**（例如针对修改的模块运行特定单测，不在此阶段跑全量 E2E）。

### 3. 记录实现细节（细节层，可选）
- 仅在涉及复杂状态迁移、不易从代码直观理解的架构取舍时，在 `implementation/{序号}-{简短描述}.md` 记录前后对比与设计理由。

### 4. 输出 Task Completion Card（进度层，硬性规则）
每个 Task 完成后，**必须**在 `tasks.md`（或对应子专题文档）中将该任务转换为标准完成卡：

```markdown
### Task X.X: {任务名称}
- **改动内容：** {具体做了什么改动}
- **代码位置：** `{包路径/源码文件}` → `{类名.函数名()}`
- **预期结果：** {改动后预期的正确行为或输出}
- **验证项：** `{真实执行的验证命令或步骤}` → ✅ 通过 / ❌ 失败（原因：xxx）
- **状态：** `[x]` 已完成 {YYYY-MM-DD} → 见 implementation/xx-xxx.md（如有）
- **合规检查：**
  - [x] 代码实现完成且通过代码格式化检查 (ruff check / format)
  - [x] 针对性 Phase 验证已真实执行并保留明确结果
  - [x] tasks.md 状态已勾选更新
  - [ ] docs/CONTEXT.md 已同步当前状态（无状态/包变动标注「跳过」）
  - [ ] docs/FEATURES.md 已同步功能状态（纯内部修复/重构标注「跳过」）
  - [ ] docs/CHANGELOG.md 已在 [Unreleased] 追加变更说明（非公开协议/能力变动标注「跳过」）
```

> ⚠️ **硬性禁令：**
> - **没有完成卡 = 任务没完成！** 严禁仅靠对话口头声称完成。
> - 前三项合规检查（代码、验证、任务更新）为强制勾选，缺一不可。
> - 完成单个任务后继续处理范围内的后续任务，不得把单个任务的完成当成整项需求的交付。

---

## 细节记录模板 (可选)

路径：`docs/projects/{YYYYMMDD}-{项目名}/implementation/{序号}-{简短描述}.md`

```markdown
# {改动标题}

## 基本信息
- **日期：** {YYYY-MM-DD}
- **对应任务：** Task X.X: {任务名称}
- **涉及包：** `libs/langhost` / `libs/langgraph-runtime-pg`

## 改动文件
- `libs/langhost/src/langhost/xxx.py`
- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/xxx.py`

## 核心设计与代码对比
### 1. {改动点说明}
- **设计决策：** {为什么采用这种方案}
- **关键代码：**
```python
# 核心片段展示
```
```
