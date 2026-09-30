# GraphHarbor 文档导航中心

> **AI 读取规则：** 新会话先读 [CONTEXT.md](CONTEXT.md)；改动涉及兼容性查阅 [compatibility/profile.md](compatibility/profile.md)。本文档供人类开发者全景导航。
> 🏠 **项目主页**：[English README](../README.md) · [简体中文主页](../README.zh-CN.md)

---

## 你在找什么？

### 🚀 第一次接触这个项目
- → **[architecture/README.md](architecture/README.md)** 了解系统架构（双包分层、ASGI 边界与 PostgreSQL/Redis 核心引擎）
- → **[compatibility/profile.md](compatibility/profile.md)** 了解对 LangGraph 官方 Core Agent Server 的支持全貌与差异

### 🛠 搭本地环境 / 跑测试套件
- → **[guides/README.md](guides/README.md)** 本地环境搭建、依赖安装、单测与 E2E 测试指南
- → **[CONTRIBUTING.md](../CONTRIBUTING.md)** 贡献指南与规范要求

### 📋 兼容性调研与协议基线
- → **[compatibility/profile.md](compatibility/profile.md)** 当前已支持与暂不支持的官方能力总表
- → **[compatibility/baseline.md](compatibility/baseline.md)** 兼容性基线与测试策略
- → **[compatibility/matrix.md](compatibility/matrix.md)** 详细端点与功能矩阵
- → **[compatibility/exclusions.json](compatibility/exclusions.json)** 官方能力排除清单

### 📦 版本发布与规范契约
- → **[CHANGELOG.md](CHANGELOG.md)** 统一版本变更历史
- → **[standards/release-process.md](standards/release-process.md)** 双包锁步发版流程与门禁规范
- → **[releases/](releases/)** 详细历史小版本发布记录（0.13.0 系列等）

### 🔧 生产运维 / 事故恢复
- → **[runbooks/incident-recovery.md](runbooks/incident-recovery.md)** 事故恢复、PostgreSQL/Redis 故障应对与包版本回滚
- → **[runbooks/production-runbook.md](runbooks/production-runbook.md)** 生产巡检、日常维护与就绪探针

### 🔍 查项目方案 / 决策背景
- → **[projects/](projects/)** 所有架构改造专项（含方案设计、任务拆解与真实验证证据）
- → **[archive/](archive/)** 历史归档方案（早期生产化设计草案、冻结决策）

---

## 🤖 AI 专用入口

| 文件/目录 | 用途 |
|---|---|
| [CONTEXT.md](CONTEXT.md) | **会话快照**：记录双包锁步版本、活跃专项进展、近期重大决策与核心边界（必读） |
| [FEATURES.md](FEATURES.md) | **功能清单**：全仓库功能与协议能力现状总览（语义记忆） |
| [standards/README.md](standards/README.md) | **规范健康表**：跨包协议契约生效状态与置信度一览 |
| [compatibility/profile.md](compatibility/profile.md) | **协议画像**：核对 LangGraph 协议实现现状与支持边界 |
| [lessons/](lessons/) | **经验库**：AI 踩坑教训库（按需读取，每条 ≤4 行） |
| [projects/](projects/) | **专项事实源**：正在实施的方案、任务进度与验证项 |

---

## 🗂 目录速查

```text
docs/
├── README.md          ← 【你在这里】人类全景导航中心
├── CONTEXT.md         ← AI: 会话记忆与项目状态快照
├── FEATURES.md        ← AI: 全仓库功能现状总览
├── CHANGELOG.md       ← 统一版本变更历史
│
├── architecture/      ← 系统架构教学与原理解析（留空待填）
├── guides/            ← 开发者指南与测试操作规范
├── compatibility/     ← LangGraph 官方协议兼容性专项库（基线/矩阵/画像）
├── standards/         ← 规范契约（双包锁步发布流程等）
├── runbooks/          ← 生产运维与故障恢复手册
├── releases/          ← 历史细分版本发布说明归档
├── lessons/           ← AI 领域踩坑经验库
├── projects/          ← 特性与重构专项（RFC / Plan / Tasks / Verification）
│
├── demo/              ← 演示页面资源
├── assets/            ← 架构图及静态资源
└── archive/           ← 历史草案与退役材料收容所
```
