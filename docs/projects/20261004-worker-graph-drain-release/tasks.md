# 发布任务

### Task 1.1: 双包版本锁步
- **改动内容：** 同步包版本、精确依赖、锁文件和版本门禁。
- **代码位置：** `libs/langhost/pyproject.toml`、`libs/langgraph-runtime-pg/pyproject.toml`、`scripts/check_versions.py`、`uv.lock`
- **预期结果：** 两个包均为 `0.13.0.post38`，CLI 精确依赖同版 runtime。
- **验证项：** `uv lock --check` → 通过；`python3 scripts/check_versions.py` → `ok: graphharbor=0.13.0.post38`
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：** `[x]` 锁步与格式通过；`[x]` 阶段验证已记录；`[x]` CONTEXT/CHANGELOG 已同步

### Task 1.2: 发布门禁及构建
- **改动内容：** 验证格式、类型、功能回归与本地四个构建产物。
- **代码位置：** `libs/`、`dist/`（构建输出）
- **预期结果：** 门禁通过，wheel 可独立导入。
- **验证项：** Ruff 与 mypy 通过；隔离库 runtime `162 passed, 18 skipped`、网关 `63 passed, 3 skipped`；四个产物构建通过，独立 wheel 导入及 CLI `--version` 通过
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：** `[x]` 构建与格式通过；`[x]` 阶段验证已记录；`[x]` 仅使用隔离数据库

### Task 1.3: 正式发布与索引核验
- **改动内容：** 修复 runtime 的 OIDC Environment；按用户指定从本地凭据直发双包，验证 PyPI 双包及 CLI。
- **代码位置：** `.github/workflows/release.yml`、`AGENTS.md`、`docs/standards/release-process.md`
- **预期结果：** PyPI 可安装同版双包，GitHub Release 成功。
- **验证项：** PyPI JSON 列出双包各 wheel/sdist；CLI 精确依赖同版 runtime；独立安装返回双包 `0.13.0.post38`，`graphharbor --version` 返回 `0.13.0.post38`；GitHub Release 附四个产物
- **状态：** `[x]` 已完成 2026-10-04
- **合规检查：** `[x]` 令牌仅注入上传进程；`[x]` 阶段验证已记录；`[x]` CONTEXT/发布规范已同步
