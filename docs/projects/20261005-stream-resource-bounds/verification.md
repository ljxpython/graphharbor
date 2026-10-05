# 验证

## Phase 验证记录
- T1：Redis 实际写入 TTL、成功/error/interrupted 终态本地缓冲回收、非终态保留通过；与分页心跳测试合计 9 passed。
- T2：完整组合回归 179 passed / 7 skipped（隔离库 graphharbor_event_retention_verify_20261005，Redis DB 15 专属前缀），包含生产 Worker、queue、持久化、REST、官方 SDK、事件过期和 Vue 协议；日志 /tmp/graphharbor-resource-gates.log。
- T3：uv lock --check、scripts/check_versions.py、ruff check/format、mypy、双包 wheel/sdist 构建通过；仓库外隔离 wheel 安装及导入版本均为 0.13.0.post39。双包上传和官方 PyPI 独立安装/CLI 已通过。
- 发现并修复测试环境污染：test_cli.py 的 serve/worker callback 覆写 DATABASE_URI=postgresql://example，使用 monkeypatch 隔离环境后组合通过。

## Final 验证记录
发布及平台安装验证通过；GraphHarbor 项目状态 done。

补充：test_public_runtime.py 18 passed；发布产物 runtime wheel/sdist 与 CLI wheel/sdist 四件成功上传，仓库外独立安装版本与 CLI 均为 post39。平台真实多会话验收通过。

Final 补充：平台使用已发布 PyPI 包运行 3 个并发会话，连续切换与后台排队全部完成，六个 Run 均 success；浏览器 1 passed（39.9s）。实际 Redis 回放 TTL 3446–3584 秒；平台 API/Worker 重启后健康正常。源码未 Git 提交。
