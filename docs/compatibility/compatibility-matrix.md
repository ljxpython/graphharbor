# 发布兼容矩阵

## 2026-09-15 v3 lifecycle增量验收

最终补丁为 [0.13.0.post30](release-notes-0.13.0.post30.md)，已发布并验证Worker保留recursion_limit；post29生命周期对齐证据仍有效。post30全量162 passed／18 skipped，实际wheel HTTP／SDK回归通过。

状态backend-verified-with-documented-differences，0.13.0.post29已发布。真实官方Server0.13.0与GraphHarbor同图采集覆盖五种event、命名子图、Send和工具分派cause；深层过滤、多中断分别恢复、SDK消息／工具、持久回放、Worker恢复及实际wheel链路通过。已修根running、child scope、graph_name与错误文本；严格差分仍记录26项差异，主要为既有status/reason/output扩展、子终态data.namespace及额外子interrupted，未将它们静默剔除或算作零差异。证据：`artifacts/v3-official-baseline.json`、`artifacts/v3-graphharbor-after.json`、`artifacts/v3-lifecycle-diff.json`及[post29发布记录](release-notes-0.13.0.post29.md)。前端／Vue验收与默认切换未包含在本结论中。

官方Run SSE传version=v3仍返回legacy数据；GraphHarbor typed Run SSE目前是扩展，不能宣称与该官方端点等价。线程Protocol兼容性与图内v3须分别验收。下面的旧发布矩阵是历史基线，不代表本次v3全部通过。

| 项目 | 已验证版本 | 说明 |
|---|---|---|
| `graphharbor` | 0.13.0.post20 | 与 runtime 锁步发布 |
| `graphharbor-runtime` | 0.13.0.post20 | 默认不依赖 `langgraph-api` |
| Python | 3.11 / 3.12 / 3.13 | CI production-contract matrix |
| `langgraph` | 1.2.11 | 锁文件固定 |
| `langgraph-sdk` | 0.4.3 | Python SDK 契约 |
| `langgraph-cli` | 0.4.31 | `langgraph.json` 校验 |
| 官方 Agent Server | `langgraph-api==0.13.0` / `langgraph dev` | 升级时作为公开输出比较基线 |
| 官方协议对照 | passed（含已记录 core profile exclusions） | 2026-08-25 本地双服务对照通过；Store 与 thread stream 已纳入门禁 |
| PostgreSQL | 16 | 本地/CI 基线 |
| Redis | 7 | 本地/CI 基线 |
| MCP transport | Streamable HTTP `/mcp/` | 本地 discovery/call 与出站 fixture 已通过；生产认证、租户隔离、跨网络故障和 legacy SSE/stdio 另行验收 |
| P0 graph 双端 | fixture runner | 五个 acceptance P0 已于 2026-08-28 对固定 `langgraph dev` 真实执行并通过；真实 Agent 比较有序 tool trace，Deep Agent 验证 `task` 委派阶段且不比较自然语言；外部 `runtime_service` graph 不在本仓库，不能由此推断 |

`langgraph-api==0.13.0` 仅用于 `compatibility` extra 的内部对照，不是生产启动依赖。每次更新本表任一版本前，必须完成锁文件、官方输出差分、Python/JavaScript SDK、持久化/故障、P0 graph 和主机部署验收。完整步骤见 [compatibility-upgrades.md](compatibility-upgrades.md)。

当前 core profile 的明确协议排除项见 [compatibility-exclusions.json](compatibility-exclusions.json)。Store 与 `/threads/{thread_id}/stream` 不再属于排除项；剩余排除项只覆盖未实现的官方扩展能力和 GraphHarbor 自有运维端点。新增排除项必须同时补充原因并经过评审。
