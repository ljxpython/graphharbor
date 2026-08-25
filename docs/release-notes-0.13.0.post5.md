# GraphHarbor 0.13.0.post5

## 修复

- JavaScript 官方 SDK 契约流水线同时启动 GraphHarbor API 和 worker。
- `threads.joinStream()` 现在在真实 worker 执行路径下验证 running lifecycle 元数据，不再因无 worker 的 pending run 无限等待。

## 验证

- Python 静态检查、官方协议比较和 SDK 契约回归通过。
- `graphharbor` 与 `graphharbor-runtime` 保持锁步发布。
