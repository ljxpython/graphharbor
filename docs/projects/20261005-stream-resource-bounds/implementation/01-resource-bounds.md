# 资源边界修复

- redis_stream.py StreamManager.put/put_batch：XADD 与 3600 秒滑动 TTL 同事务提交；Worker 崩溃也不产生永久缓存。
- production_worker.py _fanout_durable_event：持久化终态 fanout 后调用已有 clear_run_buffers，保持 Redis 一小时回放，本地帧延迟 120 秒释放。
- protocol_api.py _load_protocol_events/protocol_event_stream：每页最多 32 条，发完清空，再继续读取；避免整个历史被 SSE 闭包持有。保留 ACL、非 resumable 过滤、旧游标 410。
- test_stream_resource_bounds.py 与 test_stream_heartbeat_resilience.py 覆盖实际 Redis TTL/内存回收和多页完整性；test_cli.py 隔离 CLI 单测环境变量污染。
