# 02 模型与业务 trace 解耦

## 目标

模型选择、供应商、BYOK、工具政策、platform_trace_id 和项目/用户观测归平台。GraphHarbor 保留执行诊断与标准 graph 运行上下文；业务 graph 可以自行使用模型与观测 SDK。

## 方案设计

### 当前证据与复用点

| 仓库 / 文件 | 事实 | 改动方向 |
|---|---|---|
| G libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py | 构造 trace_context 时特判 model_id、tenant/project/user、platform_trace_id | 删除业务提取和注入；保留 run/thread/assistant/version/graph |
| G 同目录 observability.py | 默认 allowlist 有业务键；tool_names 不受 allowed_keys 约束单独汇总 | 仅通用诊断默认值，移除业务特判；保留通用摘要和脱敏 |
| G 同目录 auth.py | correlation 特判并写入固定运行上下文 | 由 01 的不透明 auth_user 承载平台数据 |
| P apps/runtime-service/src/runtime_service/observability/langfuse.py | 已有 with_langfuse_tracing、可信 metadata、脱敏和 fail-soft | 复用，不新建观测插件服务 |
| P 同目录 otel.py | 已有关联 run/thread/request/platform trace | 固化来源与关联测试 |
| P apps/runtime-service/src/runtime_service/services/dearflow_agent/agent.py、services/demo/showcase_demo/agent.py | factory 读取已验证业务身份、解析模型策略并传 trusted_metadata | 完成其他现役 factory 的一致覆盖 |
| P apps/platform-api/src/platform_api/core/security/tokens.py | create_runtime_delegation_token 尚未签发 request_id / platform_trace_id | 平台负责创建/验证来源并可选签入 |
| P apps/platform-api/src/platform_api/adapters/langgraph/runtime_gateway_upstream.py | with_forwarded_headers 合并旧、新 headers | x-request-id 不因换签丢失；无需重复“修复丢 header” |

### 目标数据流

platform-api 请求关联信息 → 签名委托 → runtime-service authenticate → 不透明 auth_user → GraphHarbor 通用签名快照 → graph factory → 平台 tracing wrapper。

模型仍通过平台受控 runtime_model_ref 和现有 resolver 解析；保留项目 BYOK、allowed_model_ids、tool_overrides、context_hash 校验。不将模型凭据放入 auth_user、metadata 或 GraphHarbor 数据列。

GraphHarbor 默认日志/事件只记录 run_id、thread_id、assistant_id、assistant_version、deployment_version、graph_id、status、通用 request_id 等运行概念。request_id 可作为通用请求关联，但不解释 platform_trace_id。业务回调产生的正常 graph 事件照常传递，不能用全局字符串清洗破坏用户 payload。

### 来源与信任

- 平台 correlation ID 来自平台可信请求上下文；外部传入值按平台既有规则验证/重新生成，限制类型和长度。即使 ID 只用于观测，也不能混作授权。
- trusted_metadata 优先于调用者 metadata；可信值缺失时不允许客户端以同名保留键伪装可信值。未知业务键不自动加入日志。
- 子图、并发子 agent、HITL 与 restart 使用对应 run/thread 关联；工具的秘密参数、JWT、auth_user 全量内容不写入 trace。
- GraphHarbor 中 `build_trace_metadata(allowed_keys=...)` 是通用工具参数，可保留；这不代表 worker 可以继续默认拼装平台数据。
- __graphharbor_runtime_context / __graphharbor_runtime_policy 若有消费者，先迁至平台 verified_delegation_from_user；在同次改动中删除旧出口，不保留转发别名或双轨适配。
- 核心出错但 graph 尚未构建时，只要求通用 run/request 定位；平台在网关/适配器记录业务关联。无需为了业务 trace 把模型数据重新塞回 worker。
- 本专项不引入全量 OpenTelemetry 架构重写，不实施旧提案中的新 traceparent 系统。以后有明确需要再独立规划。

## 任务拆分

| ID | 改动内容 / 代码位置 | 预期结果 | 验证项 | 状态 |
|---|---|---|---|---|
| B01 | P tokens.py、gateway presentation/http.py、runtime/auth.py | 按新契约统一签发和传递可信关联字段；不增加旧委托兼容路径 | V-B01 | 部分完成：签发端已支持并校验 request/platform trace；定向单测通过 |
| B02 | P observability/langfuse.py、otel.py 和现役 graph factories | 模型与业务 trace 完整，复用现有解析链 | V-B02—B04 | 部分完成：客户端同名 request/platform trace 不再进入可信 trace；37 项观测测试通过 |
| B03 | G production_worker.py、observability.py、graph_executor.py；依赖 A05 | 移除业务字段特判与默认 trace；保留通用诊断 | V-B03—B05 | 部分完成：worker 默认 trace 移除 model/tenant/project/user，事件持久化入口过滤同名业务字段，核心观测默认键同步收口；观测单测 4 项通过。本机 Redis 可用；PG 集成 fixture 因 psycopg 对本机 PostgreSQL 14 握手返回 bytes 的环境兼容错误未通过 |
| B04 | G/P 配置、示例、发行说明、边界检查 | 再引入业务特判会被检查发现 | V-B05 + Final | 待开始 |

## 验证要求与记录

- [ ] V-B01：普通读取与 scoped run 两种委托都关联 request；header 合并不回归；按新契约验证必填/可选字段与缺省行为，不以旧委托可用作为验收要求。
- [ ] V-B02：确定性假模型先跑模型允许/拒绝、BYOK 项目隔离、工具禁止、context_hash 篡改、model reference 兑换；GraphHarbor 无模型供应商知识。
- [ ] V-B03：正常、worker 错误、graph factory 错误、cancel、retry、HITL、子图并发均能关联 run；业务 trace 由平台产生，核心默认诊断没有 model/platform 字段特判。
- [ ] V-B04：伪造 model_id/platform_trace_id/tenant_id、可信字段缺失、超长 correlation、秘密参数、Langfuse 不可用；伪造不污染可信 trace，观测故障遵循既有 fail-soft。
- [ ] V-B05：identity-only graph 不装平台代码能跑；平台业务 graph 仍可合法传 model_id 等自定义数据；不使用“仓库出现关键字即失败”的粗暴扫描。

复用 G test_observability.py 和 production contract；P observability/test_langfuse.py、test_graph_tracing.py、runtime/test_modeling.py、test_tool_governance.py。正式联合烟测可在确定性验收后使用项目 AGENTS.md 约定的真实模型，凭据仅注入进程，不记入文档或证据。

**2026-09-25 调研记录：** 源码确认 header 是合并行为；JWT issuer 未发出可选 correlation claims。未执行 trace/模型测试，也不声称线上已经丢 trace。

## 状态

规划完成，待人工评审。平台已有实现是复用起点，不代表本专项完成。
