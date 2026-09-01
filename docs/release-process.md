# GraphHarbor 正式发布流程

## 当前版本发布结论

- `graphharbor` 与 `graphharbor-runtime` 必须锁步使用同一版本。
- 发布前必须通过 `uv lock --check`、`python3 scripts/check_versions.py`、Lint、Build 和 Test。
- Release 工作流会先运行完整 CI，再按顺序发布 `graphharbor-runtime` 和 `graphharbor`。
- 上游 SDK 集成测试允许 15 分钟，避免正常的依赖安装和服务启动耗尽 8 分钟硬超时。
- PyPI 使用 Trusted Publishing/OIDC；发布 job 需要对应 GitHub Environment 的审批。

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
5. 合并到 `main` 后创建匹配版本的 tag，例如 `v0.13.0.post18`，再推送该 tag。
6. 在 GitHub Actions 的 Release 工作流中检查 CI 结果，并批准 `pypi-graphharbor-runtime` 和 `pypi-graphharbor` 两个 Environment。
7. 发布完成后验证 PyPI simple index、项目元数据、版本号和 CLI：

   ```bash
   uv run --isolated --no-project --with "graphharbor==VERSION" graphharbor --version
   ```

8. 在 GitHub Release 中记录变更、产物和验证结果；若 TestPyPI 验证失败，不要直接重传同一版本，先修复并递增版本。

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
