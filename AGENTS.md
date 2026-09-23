# GraphHarbor Agent 指令

## 产品边界

GraphHarbor 是通用 LangGraph Agent Server。核心包、CLI、API、事件协议和数据库模型只能承载通用运行时概念；不得加入特定业务的模型供应商、模型名、工具名、业务策略或 trace 字段。业务配置留在使用方的 graph factory、验收图或上层服务。修改运行时前检查是否越过这条边界。

当前生产 worker 仍含 `model_id`、`platform_trace_id` 等存量业务字段；见 `docs/projects/20260909-graphharbor-business-boundary-separation/` 和 `docs/projects/20260923-worker-concurrency-event-flush/open-issues.md`。不要把存量问题误当成允许继续扩散的先例。

## 真实模型验收

需要真实模型调用时，从本机 `~/.my_best/.env` 读取 `MIAOMIAOAI_PROXY_URL`、`MIAOMIAOAI_PROXY_API_KEY`，使用 `miaomiaoai` 的 `deepseek-v4.1-flash`。这些值只能注入验收进程，不得复制进仓库、文档、测试结果或命令输出；日志与产物只记录脱敏指标。先用确定性 fixture 验证功能，真实模型用于端到端烟测，不作为稳定性能基线。

发布凭据 `UV_PUBLISH_TOKEN`、`UV_TEST_PUBLISH_TOKEN` 也在该文件，但只有用户明确要求发布时才使用。不要为开发或验证自动发布。
