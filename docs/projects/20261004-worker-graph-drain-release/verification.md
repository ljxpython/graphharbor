# 发布验证

## 验证环境

- 日期：2026-10-04
- 目标版本：双包 `0.13.0.post38`
- 数据库测试：只使用专用隔离 PostgreSQL 库，绝不连接默认业务库。

## 阶段验证

- Task 1.1：`uv lock --check` 与 `python3 scripts/check_versions.py` 均通过，锁步版本为 `0.13.0.post38`。
- Task 1.2：`uv run ruff format --check libs`、`uv run ruff check libs`、`uv run mypy` 均通过；`uv build` 产出两个 sdist 和两个 wheel；独立 wheel 导入与 `graphharbor --version` 通过。
- Task 1.3：`UV_PUBLISH_TOKEN` 仅注入 `uv publish --trusted-publishing never` 的临时子进程，先后上传 runtime 与 CLI 各两个产物；PyPI JSON 核验两包均为 `0.13.0.post38`，CLI 元数据精确依赖同版 runtime。

## 最终验证

- 隔离数据库 `graphharbor_event_retention_verify_drain_20261004`：runtime 全套 `162 passed, 18 skipped`（114.49s）。
- 隔离数据库 `graphharbor_drain_20261004_test`：网关全套 `63 passed, 3 skipped`（3.89s）。
- tag 触发的 Release run `37173582271`：完整 CI 七项与 Build 全部通过；runtime 发布因 PyPI `invalid-publisher`（Environment 名称不匹配）失败，CLI 发布跳过。修复工作流后，用户指定直发；已取消候选 run `37173877663`，避免重复上传。
- PyPI 双包均列出 `0.13.0.post38` 的 wheel/sdist；全新环境 `uv run --isolated --no-project --with "graphharbor==0.13.0.post38"` 验证双包版本一致，CLI 输出 `graphharbor, version 0.13.0.post38`。
- GitHub Release `v0.13.0.post38` 已创建并附四个构建产物。

## 最终结论

- 项目状态：`done`
- 四处状态一致性：README 为 `done`，tasks 三项均完成，本文有三项 Phase 记录与独立 Final 证据，CONTEXT 状态同步为 `done`。
