# 任务

- [x] T1 Redis 写入 TTL 与终态清理。位置 redis_stream.py、production_worker.py；成功/error/interrupted 回收、非终态保留测试通过。
- [x] T2 分页协议回放。位置 protocol_api.py；多页完整性、过滤、游标、410、断开清理回归通过。
- [x] T3 发布及安装。锁步版本/静态/构建/隔离 wheel 安装已通过；双包 wheel/sdist 已上传 PyPI；从官方 index 仓库外独立安装核验双包 0.13.0.post39 与 CLI 成功。平台依赖锁已更新，API/Worker 已重启。

T1/T2 合规检查：实现完成、验证真实执行、任务更新；CONTEXT/FEATURES/CHANGELOG 随发布更新。不提交 Git。
