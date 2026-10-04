---
status: active
last_verified: 2026-10-04
confidence: high
source_project: docs/projects/20260925-agent-harness-refresh/
---

# GraphHarbor 正式发布流程

## 当前版本发布结论

- `graphharbor` 与 `graphharbor-runtime` 必须锁步使用同一版本。
- 发布前必须通过 `uv lock --check`、`python3 scripts/check_versions.py`、Lint、Build 和 Test。
- 正式发布可从 `~/.my_best/.env` 临时读取 `UV_PUBLISH_TOKEN`，在本地按顺序直接上传 `graphharbor-runtime` 和 `graphharbor`；不得输出或落盘令牌。
- 上游 SDK 集成测试允许 15 分钟，避免正常的依赖安装和服务启动耗尽 8 分钟硬超时。
- Release 工作流提供 CI 和 Trusted Publishing/OIDC 路径；使用该路径时，PyPI publisher 的 Environment 必须与工作流完全一致。

## 一次正式发布

1. 在两个包的 `pyproject.toml` 中更新同一版本，并同步 `graphharbor` 对 runtime 的精确依赖。
2. 执行 `uv lock`，再执行 `uv lock --check` 和 `python3 scripts/check_versions.py`。
3. 本地构建并检查四个产物：

   ```bash
   rm -rf dist
   uv build --package graphharbor-runtime
   uv build --package graphharbor
   ```

4. 运行与 CI 等价的格式、静态检查和测试；确认构建产物可导入。
5. 合并到 `main` 后创建匹配版本的 tag，例如 `v0.13.0.post38`。若 tag 工作流未能完成上传，先取消仍运行的发布 job，再检查 PyPI 是否已有目标版本。
6. 使用仓库外的 `UV_PUBLISH_TOKEN` 依次执行 `uv publish` 上传 runtime、CLI 的 wheel 与 sdist；令牌只注入临时进程环境。若改走 GitHub Actions，核对 PyPI publisher 与工作流 Environment（当前 runtime 为 `graphharbor-runtime`，CLI 为 `pypi-graphharbor`）。
7. 发布完成后验证 PyPI simple index、项目元数据、版本号和 CLI：

   ```bash
   uv run --isolated --no-project --with "graphharbor==VERSION" graphharbor --version
   ```

8. 在 GitHub Release 中记录变更、产物和验证结果；若上传失败且版本已有文件，不要盲目重传同一版本，先核对已发布状态。

## 发布失败处理

- CI 失败：只修复失败阶段，重新推送修复后的 commit 和新版本 tag。
- Environment 未批准或 OIDC 失败：检查仓库、workflow 文件名、包名和 PyPI Trusted Publisher 配置。
- PyPI 显示旧版本：等待 simple index 缓存刷新后再验证，不重复上传同一版本。
- 单个包已上传而另一个失败：保留已上传版本，修复后递增 patch/post 版本并重新锁步发布。

## 长期改进

- 保持版本检查脚本作为唯一的锁步门禁。
- 为构建产物增加哈希记录和安装矩阵验证（Python 3.11、3.12、3.13）。
- 定期检查 GitHub Actions pinned action、PyPI Trusted Publisher 和 Environment 审批人。
- 发布后自动生成变更摘要，并把 PyPI、GitHub Release、安装验证链接写入 release notes。
