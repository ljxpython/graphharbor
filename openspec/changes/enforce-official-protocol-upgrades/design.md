## Context

当前 CI 分别验证 GraphHarbor 的官方 SDK 契约和上游 SDK integration；它们没有比较同一请求的服务端输出。`langgraph-api` 已锁定为 compatibility profile，可用于启动官方 `langgraph dev`，但不能成为生产依赖。

## Goals / Non-Goals

**Goals:**

- 在主动升级 LangGraph 相关版本时，以固定版本官方服务的公开输出作为 GraphHarbor 的比较基线。
- 比较确定性的 HTTP、OpenAPI 和 SSE 输出，并让任何未批准差异阻断升级。
- 在兼容矩阵中记录 GraphHarbor 发布版本对应的官方组件版本和验证状态。

**Non-Goals:**

- 不轮询或自动升级上游依赖。
- 不要求生产运行时安装或启动 `langgraph-api`。
- 不比较 UUID、时间戳、端口和其他预定义动态值，也不比较私有实现细节。

## Decisions

### 1. URL 驱动的差分命令

新增一个命令接收 `--official-url` 和 `--graphharbor-url`。CI 负责在升级 job 中分别启动 `langgraph dev` 与 `graphharbor serve`，命令只负责探测、请求、归一化和比较；因此本地、CI 与未来官方启动命令可复用同一门禁。

备选方案：在比较脚本中启动两个服务。拒绝，因为配置、数据库、端口和进程清理会与测试耦合，且不必要地扩大脚本职责。

### 2. 归一化后的公开协议比较

比较 `/ok`、`/info`、`/openapi.json`、无效资源错误和最小 run stream。JSON 递归删除明确列出的动态字段，SSE 保留 `event`、`data` 顺序及媒体类型，忽略事件 ID、UUID 与时间戳。比较失败输出 JSON Pointer 风格路径和双方值。

备选方案：字节级比较。拒绝，因为官方与实现产生的 UUID、时间戳和服务地址天然不同，无法形成有意义门禁。

### 3. 显式升级触发

新增 `workflow_dispatch` 的 compatibility-upgrade job，输入为官方版本；常规 push/PR CI 不运行该 job。升级 PR 必须运行该工作流、更新 `uv.lock`、版本映射和 release notes，差异必须先适配并通过比较。

### 4. 单一版本映射

`docs/compatibility-matrix.md` 保持可读的发布映射；基线脚本从锁文件和矩阵核对当前 GraphHarbor 版本与 LangGraph 组件版本。映射只在成功比较后更新。

## Risks / Trade-offs

- [官方服务需要 PostgreSQL/Redis] → 升级工作流使用隔离 service containers 和独立数据库/Redis 前缀。
- [上游新增动态字段] → 明确列入归一化规则并添加测试，禁止模糊的全字段忽略。
- [官方新增端点] → OpenAPI 路径/方法比较失败，要求适配或记录为 capability exclusion。

## Migration Plan

1. 增加差分命令及其单元测试。
2. 添加手动兼容升级工作流，使用当前锁定版本验证基线。
3. 更新矩阵和基线校验。
4. 以后每个 LangGraph 相关依赖升级 PR 先执行该工作流；失败时不更新映射或发布。

回滚是还原依赖、锁文件和矩阵到上一已验证映射；生产包不包含官方参考服务。

## Open Questions

- 官方当前 `langgraph dev` 对于 Extended endpoints 的可用性会随版本变化；首次基线执行时将其作为 capability exclusion 明确记录。
