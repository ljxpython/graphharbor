# 实现记录

- 增加 `runs.queue_position` 迁移，创建 Run 时分配线程内顺序。
- `claim_next()` 增加同线程 FIFO、lease、interrupt、resume 和 retry backoff 守卫。
- 显式取消在执行停止前保留 lease，避免后续 Run 抢跑。
- 新增 pending Run 的 CAS 调序/取消接口，并注册 HTTP 路由。
- GraphHarbor ruff 检查与格式检查通过。

验证受限：本机 PostgreSQL 使用 `postgres` 用户连接时密码认证失败，未能执行真实数据库并发测试。
