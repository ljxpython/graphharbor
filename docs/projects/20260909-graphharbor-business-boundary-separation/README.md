# GraphHarbor 业务边界分离

## 项目概述
- **时间：** 2026-09-09 至 2026-09-16
- **目标：** 将 graphharbor 从"通用协议层+硬编码 platform-api 业务适配"混合状态，拆分为纯通用 LangGraph Agent Server + 独立业务适配层，借鉴 open-swe 的 dispatch.py 设计在上层统一封装 agent 调用
- **负责人：** @lijiaxin
- **状态：** 规划中

## 阅读顺序
1. [01-auth-contract-extraction.md](01-auth-contract-extraction.md)：将硬编码的 platform-api delegation JWT 校验逻辑从 graphharbor 提取为可插拔的 langgraph.json auth handler
2. [02-workspace-capability-migration.md](02-workspace-capability-migration.md)：将 DeepAgentWorkspace 从 graphharbor 核心包迁移到 ai-agent-platform 业务层
3. [03-observability-allowlist-decoupling.md](03-observability-allowlist-decoupling.md)：将 observability 业务字段 allowlist 从 graphharbor 解耦为 runtime-service 配置
4. [04-platform-dispatch-layer.md](04-platform-dispatch-layer.md)：在 ai-agent-platform 实现统一 dispatch 层，封装 graphharbor 标准协议调用

## 改动范围
- **影响仓库：** graphharbor, ai-agent-platform
- **改动级别：** 链路改动（跨仓库，影响鉴权契约、workspace 管理、observability allowlist、上层调用封装）
- **预计工作量：** 5 人天

## 关键决策
1. **鉴权模型：** graphharbor 只保留通用 Principal 抽象和 ASGI middleware 骨架，具体 delegation JWT 校验逻辑由 runtime-service 的 langgraph.json `auth` handler 提供
2. **workspace 管理：** DeepAgentWorkspace 从 `graphharbor-runtime` 核心包移除，作为 runtime-service 的业务能力通过 graph factory 的 `configurable` 传入
3. **observability：** graphharbor 只提供通用 trace metadata 构建工具（run_id/thread_id/tenant_id 等），业务字段 allowlist（policy_version/model_id/tool_names）由 runtime-service observability 配置管理
4. **上层封装：** 借鉴 open-swe 的 dispatch.py，在 platform-api 实现统一 dispatch 层，封装 graphharbor 标准协议（runs.create/stream），不新增业务端点
5. **兼容性：** 保持 ai-agent-platform 现有业务功能零中断，通过环境变量开关灰度切换到新边界

## 非目标
- 不改变 graphharbor 已暴露的标准 LangGraph Agent Server 协议（assistants/threads/runs/store/crons/mcp）
- 不新增 `/platform/agents` 等业务路由到 graphharbor
- 不修改 platform-web 前端调用链路（仍走 platform-api gateway）
