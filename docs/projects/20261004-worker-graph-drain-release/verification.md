# 发布验证

## 验证环境

- 日期：2026-10-04
- 目标版本：双包 `0.13.0.post38`
- 数据库测试：只使用专用隔离 PostgreSQL 库，绝不连接默认业务库。

## 阶段验证

- Task 1.1：`uv lock --check` 与 `python3 scripts/check_versions.py` 均通过，锁步版本为 `0.13.0.post38`。
- Task 1.2：`uv run ruff format --check libs`、`uv run ruff check libs`、`uv run mypy` 均通过；`uv build` 产出两个 sdist 和两个 wheel；独立 wheel 导入与 `graphharbor --version` 通过。

## 最终验证

- 隔离数据库 `graphharbor_event_retention_verify_drain_20261004`：runtime 全套 `162 passed, 18 skipped`（114.49s）。
- 隔离数据库 `graphharbor_drain_20261004_test`：网关全套 `63 passed, 3 skipped`（3.89s）。
- Release 工作流、PyPI 索引与已发布包安装：待执行。

## 最终结论

- 项目状态：`partial`（发布进行中）
