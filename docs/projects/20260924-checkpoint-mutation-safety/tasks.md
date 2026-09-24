# 任务与完成状态

**状态：partial。** 用户已授权完整实施，核心代码已落地；以下不把未执行项算成通过。

- [x] 追踪生产/旧入口与 saver 能力，确认默认依赖 stub。
- [x] prune 入参、去重、授权 ID、事务内保留集；旧 profile 删除授权事件。
- [x] 007 基线表迁移、首次 claim 捕获 checkpoint/write/thread 投影。
- [x] run/lease/generation 写屏障；claim 防抢跑；租约终态不续期。
- [x] rollback 原子恢复、删除目标 run；单个/批量共享；安全冲突 409。
- [x] worker 等待执行退出、持久意图逐项重试、独立新进程恢复。
- [x] namespace/DeltaChannel/HITL prune 保留与继续执行。
- [x] 注入恢复失败证明事务原子性，旧整 thread 清理断言替换。
- [x] PostgreSQL、生产契约、REST、官方 SDK 回归；ruff、7 文件 mypy。
- [x] 006→007→006→007 隔离迁移与 schema check。
- [x] 官方 0.13.0 in-memory 实机基础探针，登记 terminal/prune 差异。
- [ ] 官方完整差分：running、批量、重复、非法请求、授权混合矩阵。
- [ ] 大历史与高并发压力：全量基线的存储/claim 延迟，以及维护竞争。
- [ ] 发布环境预演与负责人验收；不自动发布或提交。
