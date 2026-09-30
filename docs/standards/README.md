# 跨包规范与契约健康表 (docs/standards/)

本文档是 GraphHarbor 规范契约的健康看板。各标准文件头部均挂载置信度元数据，供人类开发者与 AI 协同治理。

---

## 🚦 规范契约健康度一览表

| 规范名称 | 适用包 | 状态 | last_verified | 置信度 | 来源项目 | 核心职责 |
|---|---|---|---|---|---|---|
| **[rest-sse-contract.md](rest-sse-contract.md)** | `libs/langhost` | `active` | 2026-09-30 | 🟢 high | `20260927-sse-stream` | Core REST 端点、SSE 事件帧、15s 心跳保活与 Last-Event-ID 重播 |
| **[release-process.md](release-process.md)** | 根目录 / 双包 | `active` | 2026-09-30 | 🟢 high | `20260925-agent-harness` | 双包锁步发版门禁、TestPyPI 验证与 GitHub 环境审批 |

---

## 📜 置信度规则 (Confidence Rules)

AI 查阅具体规范文件时，必须先看其 Frontmatter 中的 `last_verified` 字段：

- 🟢 **`confidence: high`**（`< 60 天`）：最新权威事实，可直接作为代码实现与验证的强约束依据；
- 🟡 **`confidence: medium`**（`60–180 天`）：仍可参考，但代码可能已有超前演进，需配合当前代码核对；
- 🔴 **`confidence: low`**（`> 180 天`）：视为过期草案，不作硬性约束，以当前运行代码为准。

---

## ⚖️ 代码与文档冲突裁决规则

- **代码** = 当前系统运行的事实源（Source of Truth）；
- **标准文档** = 团队已批准的设计契约（Design Intent）；
- **裁决流程**：
  1. 发现代码与标准不一致时，AI 以当前代码行为为基准，向人类指出具体差异；
  2. 由人类工程师裁决是“修复代码 Bug”还是“更新标准文档”；
  3. **严禁凭直觉裁决**：涉及**持久化破坏、鉴权安全性或对外公开协议破坏**的冲突，AI 必须立刻停止并等待人工确认！

---

## 🎓 标准文件毕业机制 (Promotion)

- 当某项重构或新功能在 `docs/projects/` 中立项时，对应的规范文件初始标为 `draft`；
- 当该项目的 `verification.md` 达成 `done` 终态时，AI 在收尾反思中自动将规范的 `status` 提升为 `active`，并刷新 `last_verified` 日期；
- `partial` 或 `blocked` 状态**严禁毕业**，防止半成品伪装成权威标准。
