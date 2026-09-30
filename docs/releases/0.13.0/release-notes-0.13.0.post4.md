# GraphHarbor 0.13.0.post4

## 修复

- 修复官方 SDK/REST 契约测试在完整 CI 测试收集顺序下的 PostgreSQL/Redis 运行时生命周期隔离。
- 保持 `Store`、线程 SSE、Protocol v2 和 OpenAPI 契约验证覆盖。
- 发布流水线使用已配置的 `graphharbor-runtime` Trusted Publishing 环境。

## 验证

- Python 3.11：32 passed，1 skipped。
- 两个包继续锁步发布：`graphharbor` / `graphharbor-runtime`。
