# GraphHarbor 0.13.0.post6

## 修复

- JavaScript 官方 SDK 契约在验证和资源清理后显式退出，避免 SSE 长连接句柄阻塞 CI。

## 验证

- Python 静态检查、官方协议比较和 SDK 契约回归通过。
- `graphharbor` 与 `graphharbor-runtime` 保持锁步发布。
