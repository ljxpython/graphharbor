# 事故恢复与发布回滚

## API/worker 故障

API 无状态，可直接滚动重启。worker 停止接收新任务后，PostgreSQL lease/reaper 会将过期 claim 重新排队；检查 `/ready`、队列深度和 `runs.reason`，不要手动改 run 终态。

## PostgreSQL/Redis 故障

PostgreSQL 是 run、checkpoint、lease 和事件终态事实源；恢复数据库后先确认 `/ready`，再启动 worker。Redis 只承载队列、Pub/Sub、取消和 replay，重启不会删除 PostgreSQL 状态；客户端用最后的 `Last-Event-ID` 重连。

## 包回滚

1. 停止新流量并保留当前数据库 schema。
2. 将 API/worker 镜像或虚拟环境回退到上一锁步版本。
3. 仅在兼容矩阵允许时执行 migration downgrade；首选向前兼容的代码回滚。
4. 运行 `/ready`、迁移 current、一个最小 run 和 SSE replay 验证。

TestPyPI 预览不能覆盖同版本文件；回滚依靠部署镜像/环境回退，不删除已发布包。
