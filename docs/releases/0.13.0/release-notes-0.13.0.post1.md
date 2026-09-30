# GraphHarbor 0.13.0.post1

## 重点

- 自有无 License Key Agent Server API，PostgreSQL 持久化、Redis 队列与可恢复 SSE。
- 官方 Python/JavaScript SDK、REST、P0 graph、JWT Principal 和 HITL/cancel 契约覆盖。
- API/worker 分离、lease/reaper、基础设施重试和 PG/Redis 重启恢复。
- 采用主机 PostgreSQL/Redis 的纯 Python 运行方式；不提供 Docker Compose 或 Kubernetes 部署资源。

## 兼容矩阵

| 项目 | 版本 |
|---|---|
| GraphHarbor / runtime | 0.13.0.post1 |
| Python | 3.11 / 3.12 / 3.13 |
| LangGraph | 1.2.11 |
| langgraph-sdk | 0.4.3 |
| langgraph-cli | 0.4.31 |
| PostgreSQL | 16 |
| Redis | 7 |

`langgraph-api==0.13.0` 仅在 compatibility extra/内部对照 profile 使用，生产默认依赖不包含它，也不需要 `LANGGRAPH_CLOUD_LICENSE_KEY`。

## 当前限制

`store`、MCP、A2A、Generative UI、多区域 HA 和 TestPyPI 隔离安装属于后续 release gate，不应宣称已完成。
