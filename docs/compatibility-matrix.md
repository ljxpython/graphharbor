# 发布兼容矩阵

| 项目 | 已验证版本 | 说明 |
|---|---|---|
| `graphharbor` | 0.13.0.post1 | 与 runtime 锁步发布 |
| `graphharbor-runtime` | 0.13.0.post1 | 默认不依赖 `langgraph-api` |
| Python | 3.11 / 3.12 / 3.13 | CI production-contract matrix |
| `langgraph` | 1.2.11 | 锁文件固定 |
| `langgraph-sdk` | 0.4.3 | Python SDK 契约 |
| `langgraph-cli` | 0.4.31 | `langgraph.json` 校验 |
| PostgreSQL | 16 | 本地/CI 基线 |
| Redis | 7 | 本地/CI 基线 |

`langgraph-api==0.13.0` 仅用于 `compatibility` extra 的内部对照，不是生产启动依赖。每次更新本表任一版本前，必须完成锁文件、Python/JavaScript SDK、持久化/故障、P0 graph 和主机部署验收。
