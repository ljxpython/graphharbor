# Runtime Service 生产验收边界

本文记录 GraphHarbor 接入 `ai-agent-platform/apps/runtime-service` 后的真实验收边界。

## 已验证

- 未修改 `runtime_service/langgraph.json`。
- 9 个注册 graph 均可在 runtime-service 的 Python 3.13 环境导入，并暴露
  `ainvoke`、`astream_events`。
- 5 个 P0 graph（`assistant`、`test_case_agent_v2`、
  `customer_support_handoffs_demo`、`deepagent_demo`、`personal_assistant_demo`）均声明相同
  `RuntimeContext` schema。
- `auth.path` 加载为 `platform_auth`；delegation JWT 可被 GraphHarbor middleware 验证，
  Core 与 custom routes 读取同一个 `Principal`。
- `http.app` 加载为 custom FastAPI app，`/internal/capabilities/tools` 和
  `/internal/capabilities/models` 挂载在同一 ASGI server 上，并受认证保护。
- GraphHarbor 包入口采用懒加载，导入 graph registry 不再被旧兼容模块或迁移依赖阻断。
- thread-scoped run stream 会把 `thread_id` 传递到创建 run；worker 使用公开的
  `Runtime(ServerInfo)` 注入 Principal 身份，真实 graph 不再因 checkpointer 或 runtime user
  缺失而失败。

## P0 graph 执行前置条件

导入成功不等于调用成功。真实 run 还需要：

| 依赖 | 影响 graph | 验收要求 |
|---|---|---|
| 模型 provider API key/base URL | 所有调用模型的 graph | 使用平台测试凭据，或注入实现 `bind_tools`、`ainvoke`、`astream` 的受控 chat model |
| LightRAG MCP `127.0.0.1:8621/sse` | `test_case_agent_v2` | 启动对应 MCP 服务；不可用时只允许记录为外部集成失败 |
| deepagent skills 目录 | `deepagent_demo`、`test_case_agent_v2` | 提供配置的 skills 根目录；路径缺失必须显式失败 |
| Tavily MCP | `research_demo` | 仅在该 graph 纳入 P1 时提供 Tavily 凭据和服务 |
| PostgreSQL/Redis | 所有生产 HTTP run | 先执行 migration，再启动 API/worker；两者均为共享事实源/传输层 |

## 受控 fake 规则

受控 fake 只用于协议、HITL、取消、重试和 replay 验收，不替代真实 provider 集成。fake
必须支持工具绑定（`bind_tools`）以及异步调用/流式调用；简单的
`FakeListChatModel` 不支持工具绑定，不能作为 P0 agent 的执行 fake。

真实 provider 和 MCP 集成应使用独立的 live 测试标记，凭据缺失时跳过并输出明确原因，
不得把跳过当成生产通过。

## 当前 live P0 结果

2026-08-25 已使用 `open-swe/.env` 的 DeepSeek 配置（仅在验收进程中注入）完成真实验收：五个
P0 graph 的官方 Python SDK v2 stream 全部通过，耗时 21.11 秒；API、worker、认证、PostgreSQL、
Redis、模型调用和 run 持久化链路均已打通。此前 provider 返回 Cloudflare 403 的旧结果已被替换。
MCP/skills 等外部集成仍需按上表分别满足依赖并做专门场景验收。

## 本地可靠性结果

2026-08-25 使用独立的慢图验收拓扑（API、worker、PostgreSQL、Redis）验证了队列积压、worker
滚动停机与 replacement worker 接管、API 重启后的 run 查询、PostgreSQL/Redis 重启和 SSE replay。
Redis 运行中重启的 slow run 最终为 `success`；PostgreSQL 重启后的三条 run 也都为 `success`，其
持久化 `retry_count` 为 1、1、2。使用 `Last-Event-ID: 1` 重连 run stream 时，返回后续事件和终态
`end`。这些结果仅证明本地单机故障恢复，不替代跨网络长连接验收。
