# GraphHarbor 开发者指南导航

本文档汇集 GraphHarbor 本地开发、环境搭建、测试执行与工作流指南。

---

## 🛠 开发快速入口

### 1. 本地环境搭建
- **基础依赖**：Python 3.11+, [uv](https://docs.astral.sh/uv/), PostgreSQL 16+, Redis 7+。
- **环境初始化**：
  ```bash
  uv sync --group dev
  cp .env.example .env
  # 配置 .env 中的 DATABASE_URI 与 REDIS_URI
  ```

### 2. 代码检查与测试
- **静态代码检查**：
  ```bash
  uv run pre-commit run --all-files
  uv run ruff check .
  uv run ruff format --check .
  ```
- **快速单元测试**（PostgreSQL/Redis 启动后）：
  ```bash
  uv run pytest -q libs/langgraph-runtime-pg/tests
  ```
- **端到端全量验证**（⚠️ 注意：会清空测试数据库）：
  ```bash
  ./scripts/test.sh
  ```

---

## 📖 相关规范与进阶文档

- [CONTRIBUTING.md](../../CONTRIBUTING.md) — 社区贡献准则与双包锁步发版约定
- [standards/release-process.md](../standards/release-process.md) — 正式发布流程与双包发布门禁
- [compatibility/profile.md](../compatibility/profile.md) — LangGraph 官方协议能力兼容全貌
