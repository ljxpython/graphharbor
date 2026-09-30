# GraphHarbor Agent 协作规则更新

## 概览

- **日期：** 2026-09-25
- **目标：** 让 `AGENTS.md` 与三份项目 Skill 准确描述 GraphHarbor 的边界、规划、实施和验证流程。
- **范围：** 仓库级开发规范、`.codex/skills/` 与其跟踪规则；不修改运行时代码或对外协议。
- **级别：** 治理改动，改变后续任务的工作规则。
- **状态：** done；九项规则改造任务和文档校验完成，未来功能仍须逐项目验证。

## 导航

- [方案与取舍](plan.md)
- [实施任务](tasks.md)
- [验收计划](verification.md)

## 决策摘要

1. `AGENTS.md` 保持常驻、简短，只定义仓库边界、分级与触发规则；Skill 负责各阶段操作细节。
2. 分级按影响面与风险，不按文件数或是否“跨服务”。GraphHarbor 是单仓库双包，公开 API、事件协议、持久化和发布链路可以跨包，也可以只改一个文件。
3. 沿用现有 `docs/projects/` 标准 / 多专题格式；`AGENTS.md` 定义工作规则，Skill 生成的项目文档承载方案、任务和验证证据。
4. 借鉴平台仓库的状态一致性与按需读取经验，但不预设 `docs/CONTEXT.md`、`docs/FEATURES.md`、`docs/lessons/` 必须创建。
5. `.gitignore` 仅放行仓库 Skill，保留本机 `.codex` 配置的忽略规则。
6. 补上任务四字段、阶段与最终验证、风险对应证据和重复失误的反馈位置；过程只能降低漏检风险，不能替代真实测试。

## 依据

- 平台仓库 `AGENTS.md`、`docs/knowledge/ai-harness-theory.md`、`ai-harness-practice.md` 和三份同名 Skill。
- 本仓库 `AGENTS.md`、`.codex/skills/`、`CONTRIBUTING.md` 与现有 `docs/projects/` 实例。
