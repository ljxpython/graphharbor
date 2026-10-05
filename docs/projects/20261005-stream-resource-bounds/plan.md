# 方案

平台现场 Redis 18 GB，API footprint 6.6 GB。ProductionWorker 未调用旧 Runs.enter 的清理逻辑；protocol_event_stream 在回放前全量载入所有 payload，且闭包保留 replay。

修复终态缓存 TTL 与写入兜底 TTL，避免崩溃遗留永久键；保持 PG 权威事件。回放按页读取并及时释放，保持游标、410 和 ACL 语义。回放缓冲不得长期挂在活跃 SSE 闭包中。双包锁步 post39，隔离库测试、lint/type/build/安装后 PyPI 发布，再由平台更新依赖验证。

不自动更改共享 Redis 全局淘汰策略，避免丢失任务/心跳。已有缓存由平台工具仅清已核实过期终态，未知保留。

状态：done；post39 已发布，平台依赖升级重启与真实浏览器验收通过。平台后续获得用户明确授权，另清理 618 个 PG 无 Run 记录的测试遗留缓存。
