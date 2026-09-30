# GraphHarbor Agent 指令

## 仓库入口与事实来源

本仓库有两个可发布包：`libs/langhost/` 承载 GraphHarbor CLI/API，`libs/langgraph-runtime-pg/` 承载 PostgreSQL/Redis runtime。客户端可见边界包括 CLI、REST、SDK、SSE 事件和持久状态；不要把“只改一个文件”误当成局部改动。

- 以当前代码、测试和 `docs/compatibility/profile.md` 核对已支持的能力；新会话建议先查阅 `docs/CONTEXT.md`；`docs/projects/` 保存正在实施的方案、任务和实际验证证据，不能用计划代替实现事实。
- 开发命令见 `CONTRIBUTING.md` 与 `docs/guides/`。`scripts/test.sh` 会清空测试库中的 PG 表并启动服务，只能在确认隔离、可丢弃的测试环境运行。
- `AGENTS.md` 定义长期边界和流程触发；`.codex/skills/` 分别定义规划、实施和验证步骤。按任务读取相关文档，不在每次会话加载全部历史项目。

## 改动分级

按影响与风险取较高级别，而非按文件数或代码行数判断：

| 类型 | 判断依据 | 动作 |
| --- | --- | --- |
| 讨论 | 只问分析、可行性或选择，未要求落地 | 直接回答；用户明确点名 Skill 时按要求执行 |
| 局部 | 不改变公开契约、持久数据语义或生产安全边界 | 直接实现，运行相关测试与适用质量检查 |
| 链路 | 改变公开 CLI/REST/SDK/SSE 契约，或跨 API、worker、存储消费边界 | 先用 `plan-project` 明确预期行为与链路验证，再实施并用 `verify-change` 收尾 |
| 治理 | 架构、安全、权限、迁移、恢复、生产运维、发布门禁或协作规则 | 用 `plan-project` 明确风险、回滚和证据，再按影响面验证 |

已有同目标项目时更新原项目。用户只要求规划时止于方案；已授权实施时继续，不重复索要许可。项目实施读取 `implement-feature`；完成后读取 `verify-change`。局部改动不强制创建项目文档。

## 交付质量底线

- 改动前确认真实调用方、受影响的对外契约和失败路径；修改公开行为时在任务中写出兼容预期和可执行验收项。没有预期结果与验证方式的任务不能标完成。
- 行为变化至少留下能失败的相关检查。公开协议关注 SDK/REST/SSE 消费方与错误语义；持久化关注数据隔离、迁移、重启与重放；鉴权关注未授权和跨租户访问。只执行本次影响所需的检查，不能用无关的全量测试替代缺失的关键证据。
- 每个项目任务记录局部验证；项目结束前核对任务、最终验证证据和 README 状态。失败、跳过、未运行以及外部阻塞必须如实记录；缺必要证据不能写 `done` 或宣称兼容。
- 文档/Skill 改动检查触发场景、引用路径和规则冲突；它们不需要运行无关的服务 E2E。
- 涉及打包或发布时核对两个包的锁步版本、构建产物和安装行为；具体门禁见 `docs/standards/release-process.md`。没有用户明确发布指令时只完成本地准备和验证。

严禁编造测试结果、把未运行写成通过、把 `implementation/` 当进度来源，或在没有隔离测试库的情况下运行会清空数据的测试。不自动 `git commit`、推送或发布。

重复出现的跨任务失误才提炼进本文件的通用禁令；某一流程的细节只改对应 Skill。项目特有的结论留在该项目文档，不为一次错误增加全局规则。

## 产品边界

GraphHarbor 是通用 LangGraph Agent Server。核心包、CLI、API、事件协议和数据库模型只能承载通用运行时概念；不得加入特定业务的模型供应商、模型名、工具名、业务策略或 trace 字段。业务配置留在使用方的 graph factory、验收图或上层服务。修改运行时前检查是否越过这条边界。

当前生产 worker 仍含 `model_id`、`platform_trace_id` 等存量业务字段；见 `docs/projects/20260909-graphharbor-business-boundary-separation/` 和 `docs/projects/20260923-worker-concurrency-event-flush/open-issues.md`。不要把存量问题误当成允许继续扩散的先例。

## 真实模型验收

需要真实模型调用时，从本机 `~/.my_best/.env` 读取 `MIAOMIAOAI_PROXY_URL`、`MIAOMIAOAI_PROXY_API_KEY`，使用 `miaomiaoai` 的 `deepseek-v4.1-flash`。这些值只能注入验收进程，不得复制进仓库、文档、测试结果或命令输出；日志与产物只记录脱敏指标。先用确定性 fixture 验证功能，真实模型用于端到端烟测，不作为稳定性能基线。

发布凭据 `UV_PUBLISH_TOKEN`、`UV_TEST_PUBLISH_TOKEN` 也在该文件，但只有用户明确要求发布时才使用。不要为开发或验证自动发布。
