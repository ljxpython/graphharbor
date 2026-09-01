# GraphHarbor local acceptance app

These graphs are test fixtures, not product APIs. They stay versioned with the
compatibility contract so a dependency upgrade runs the same graph semantics.

The `basic`, `subgraph`, `hitl`, and `tool` graphs are deterministic. `chat`
calls the OpenAI-compatible DeepSeek proxy configured by the process environment
and records provider streaming facts in its final state. Credentials are never
written to acceptance artifacts.

From the repository root, after PostgreSQL, Redis, API, and worker are running:

```bash
uv run python tests/acceptance_app/run_acceptance.py \
  --base-url http://127.0.0.1:31296 \
  --official-url http://127.0.0.1:31398 \
  --require-official \
  --with-chat --with-agents --with-javascript
```

`--official-url` 必须指向本次固定版本的 `langgraph dev`；`--require-official` 确保
没有双端对照时直接记为 `not_run` 并失败。结果写入
`artifacts/compatibility-result.json`，包含 LangGraph、LangChain、Deep Agents、真实
DeepSeek、Store、HITL、v3 typed projection、SSE replay 和 JavaScript SDK 证据。

默认门禁只接受 `passed`。真实 DeepSeek 不可达或凭据缺失会记为
`blocked_external_dependency`，不会算通过；仅在本地诊断时显式加
`--allow-incomplete` 才允许非零门禁被放宽。

复杂 P0、MCP 和网络层验收：

```bash
uv run python tests/acceptance_app/run_acceptance.py \
  --base-url http://127.0.0.1:31296 \
  --with-p0 --with-mcp --with-network-sse
```

`--with-p0` 覆盖 supervisor/map-reduce、handoff/HITL、LangChain 测试 Agent、个人助理
和 Deep Agent planning/subagent。若同时提供 `--official-url`，统一 runner 还会让五个
P0 graph 分别在固定版本 `langgraph dev` 与 GraphHarbor 上执行并比较结构化不变量。
真实 Agent P0 比较有序工具调用 trace 与次数，不比较模型自然语言全文；因此模型跳过、
重复或颠倒必需工具调用会直接失败。Deep Agent 的连续 `write_todos` 进度更新会归一化，
但研究前后阶段与 Deep Agent `task` 委派调用仍必须严格存在且顺序正确；待办进度次数可变，
所以该 graph 使用有序子序列契约，不把模型内部进度粒度误判为平台兼容性差异。
`--with-mcp` 同时覆盖外部 MCP client 集成和 GraphHarbor 自身 `/mcp/` transport；外部
fixture 需要先启动：

```bash
uv run --group acceptance python tests/acceptance_app/mcp_server.py
```

跨网络 SSE 不是 loopback 门禁。将服务暴露在第二台受控主机或故障代理后，再运行：

```bash
uv run python tests/acceptance_app/run_acceptance.py \
  --base-url http://127.0.0.1:31296 \
  --cross-network-sse-url https://受控远端地址
```

脚本会让客户端断开后使用 `Last-Event-ID` 重连，并断言无重复、无丢失且收到终态
values 投影；同时读取 run status 确认最终为 `success`。loopback 只能证明协议路径，
只有 `--cross-network-sse-url` 指向第二台受控主机或故障代理时才算跨网络门禁。

真实 worker 崩溃和 checkpoint 接管验收：

```bash
uv run python tests/acceptance_app/run_fault_injection.py \
  --database-uri "$DATABASE_URI" \
  --redis-uri "$REDIS_URI"

uv run python tests/acceptance_app/run_fault_injection.py \
  --database-uri "$DATABASE_URI" \
  --redis-uri "$REDIS_URI" \
  --worker-signal SIGTERM
```

脚本会启动隔离 API/worker，等待第一阶段 checkpoint 后终止 worker，再启动 replacement，
并从 PostgreSQL 校验同一个 `run_id`、`retry_count` 和唯一 terminal event。`SIGTERM` 模式
还要求持久化 `shutdown_requeue` 生命周期事件；默认 `SIGKILL` 模式验证 lease 到期接管。

普通 acceptance runner 还会自动执行 `run_terminal_idempotency.py`，验证重复 Redis
队列 hint、迟到 finalize、cancel 和 lease reaper 并发时，同一个 `run_id` 最终只有一个
terminal event 且 lease 被清理。
