# GraphHarbor 0.13.0.post8

## 修复

- 让官方协议 SDK 多中断恢复测试的断连假实现与真实 Redis 广播一致，由持久化事件处理重连回放。

## 验证

- Python 静态检查、官方协议比较和 SDK 契约回归通过。
- `graphharbor` 与 `graphharbor-runtime` 保持锁步发布。
