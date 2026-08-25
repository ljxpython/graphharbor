## Why

GraphHarbor 已通过官方 SDK 契约覆盖核心资源，但当前没有把同一输入在 GraphHarbor 和固定版本官方 Agent Server 上进行输出比较。因此依赖升级时无法证明 HTTP、SSE 和 SDK 可见输出仍以官方实现为准。

## What Changes

- 新增升级专用的官方协议差分门禁：同一套请求同时运行于 GraphHarbor 与固定版本官方 Agent Server，并比较归一化后的 HTTP、SSE 和 SDK 可见结果。
- 将 LangGraph 相关依赖升级作为显式 CI 输入；只有该输入启用时才运行差分门禁，不轮询或自动追踪上游版本。
- 建立并校验 GraphHarbor 发布版本与 `langgraph`、`langgraph-sdk`、`langgraph-cli`、官方 Agent Server 对照版本的映射记录。
- 将差异视为适配工作：除 UUID、时间戳等预定义动态字段外，协议差异必须修复或明确记录为未支持能力，才能更新映射。

## Capabilities

### New Capabilities

- `official-protocol-upgrades`: 固定官方版本、输出差分和版本映射的升级验收规则。

### Modified Capabilities

- 无。

## Impact

- 影响兼容性矩阵、基线校验脚本、CI 工作流和协议契约测试。
- 复用现有 `langgraph-api` compatibility profile 与官方 Python/JavaScript SDK；生产运行时仍不依赖 `langgraph-api`。
