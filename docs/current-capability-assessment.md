# GraphHarbor 当前能力评估与真实验收方案

更新日期：2026-08-28  
评估对象：GraphHarbor `v0.13.0.post13`（待发布本次变更）

## 1. 结论

GraphHarbor 已经实现了自有的 Agent Server、PostgreSQL 持久化 runtime 和 Redis
传输层，不是仅包装 `langgraph dev`。当前代码和已有测试足以证明大部分 Core
协议能力已经存在；但尚不能据此宣称全部真实生产场景均已验收。

最准确的当前表述是：

> GraphHarbor 已实现并通过固定版本 `langgraph dev` 对照、本地 PostgreSQL/Redis
> 真实 Agent 验收的自托管 LangGraph Agent Server Core profile；最终生产发布仍待
> 真实网络 SSE、跨版本隔离安装和发布回滚验收。

本轮完整 acceptance 结果共 `21/21 passed`。其中官方对照目标明确为同一固定版本的
`langgraph dev`，不是泛指“官方服务”；双端使用相同场景执行并按结构化协议结果差分。

Docker 不是运行前提。生产和本地验收均可直接使用宿主机 PostgreSQL 与 Redis。
但 Redis 是完整链路的必需依赖：它承担队列唤醒、Pub/Sub、取消控制和短期事件
回放；只有 PostgreSQL 时不能验证完整运行语义。

## 2. 已实现且有测试证据的能力

| 能力 | 当前实现 | 已有证据 |
|---|---|---|
| 自有 Agent Server | Starlette API、`graphharbor serve`、标准 `langgraph.json` 加载 | REST/OpenAPI、CLI、官方协议差分 |
| 资源 API | assistants、threads、runs、crons、state/history、Store | REST、Python SDK、JavaScript SDK 契约 |
| 持久化 | PostgreSQL 保存 run、thread、checkpoint、lease、事件游标 | persistence/production contract |
| 后台执行 | Redis 队列提示 + PostgreSQL `FOR UPDATE SKIP LOCKED` claim + durable lease | queue、multi-worker、reaper 测试 |
| 流式协议 | run SSE、thread stream、Protocol v2/v3 commands/events、replay | SSE、cursor/reconnect、`langgraph dev` 双端差分 |
| HITL | `interrupt`、checkpoint、`Command(resume=...)`、interrupted 状态 | SDK/protocol/production contract |
| 鉴权 | Principal、delegation JWT、tenant/project 资源隔离 | auth/production contract |
| 运行恢复 | API/worker/Redis/PostgreSQL 重启、retry、replacement worker | 2026-08-25 本地故障验收记录 |

Store 与 `/threads/{thread_id}/stream` 已实现、通过 REST/SDK/差分测试，且已从
`docs/compatibility-exclusions.json` 移除。它们的产品分级仍是 Extended，而非
第一版 Core 发布承诺；后续文档同步应表达为“已实现并验证的 Extended 能力”，
不能把历史范围决策篡改成 Core。

## 3. 尚未完成或尚未在真实环境重跑的能力

| 项目 | 当前状态 | 缺口 |
|---|---|---|
| 真实网络 SSE | 本机断线/replay 已验证 | 需要第二客户端或受控远端连接验证网络抖动后的重连 |
| v3 typed projection | 固定 `subgraph` graph 已通过真实 HTTP/SSE 端到端断言 | 仍需跨网络连接抖动场景 |
| acceptance P0 graph | 五个复杂 fixture 已与固定 `langgraph dev` 双端真实执行并通过 | 不等于外部 `runtime_service/langgraph.json` 的生产 graph 已重跑；该源码不在当前工作区 |
| MCP transport | GraphHarbor `/mcp/` 已提供 Streamable HTTP、stateless graph tools | 仍需生产 JWT/tenant 场景和远端网络故障验收；legacy SSE/stdio 不在本实现 |
| 内部 MCP graph | 已验证 Agent 作为 MCP client 调用外部 Streamable HTTP server | 外部 MCP 不等于 GraphHarbor inbound transport；两条能力分别记录 |
| 发布门禁 | 未完成 | Python 3.11/3.12/3.13 隔离安装、TestPyPI、回滚验收 |

官方 LangChain 文档确认：thread Protocol v2 stream 用请求体中的 `since` 重放；
HTTP SSE stream 以最后事件 ID 只返回后续事件。HITL 必须依赖 checkpointer，并由
`Command(resume=...)` 恢复。v3 事件流应验证 raw envelope、typed projection 和
subgraph namespace。

## 4. 建议的独立验收应用

为避免依赖缺失的业务工程阻塞平台验收，在本仓库下建立一个只用于测试的独立
`acceptance_app/`，采用标准 `langgraph.json`，不修改 GraphHarbor 生产代码。
它不是原五个业务 P0 graph 的替代品，而是验证 GraphHarbor 协议/runtime 的可复现
证据。

| Fixture graph | 验证目标 | 是否调用模型 |
|---|---|---:|
| `basic` | assistant/thread/run、PostgreSQL state/history、v2 stream | 否 |
| `chat` | 通过 OpenAI-compatible DeepSeek proxy 的真实 token stream | 是 |
| `subgraph` | 父子图、namespace/path、v3 event projection | 否 |
| `hitl` | interrupt、checkpoint、command resume、重复 resume | 否 |
| `tool` | LangChain tool call、custom event、取消/错误投影 | 可选 |

`chat` 只使用 `~/.my_best/.env` 中必要的 `DEEPSEEK_PROXY_URL`、
`DEEPSEEK_PROXY_API_KEY` 和默认模型；测试进程不得打印、写入或提交这些值。它验证
模型提供商与 GraphHarbor 的真实串接，但不替代工具、MCP 或业务权限的验证。

LangGraph/ LangChain MCP 可用于查阅官方协议与 API；本会话已启用官方
`langchain-docs` 与 `langchain-reference` MCP。它们是只读文档工具，不是待测服务。
如需验证业务 graph 内部的 MCP 调用，应另起一个最小本地 MCP fixture，独立记录其
可用性和失败策略。

## 5. 实施前置条件

1. 启动本地 Redis，并确认验收使用的 DB/prefix 与其他服务隔离。
2. 为验收创建独立 PostgreSQL 数据库，例如 `graphharbor_e2e_<timestamp>`；禁止对
   现有 `langgraph` 库运行会清空数据的测试。
3. 使用独立 Redis 端口或唯一 `GRAPHHARBOR_REDIS_PREFIX`，避免测试事件串扰。
4. 对独立数据库运行 `graphharbor migrate upgrade`，再分别启动 API 和 worker。
5. 若要重跑文档中五个 P0 graph，必须提供实际 `runtime_service` 项目根目录及其外部
   依赖、custom auth、MCP/skills 服务的测试配置。
6. 跨网络 SSE 验收必须由第二台受控主机或受控隧道执行；本机 loopback 不可替代。
7. 运行会调用 `claim_next()` 的 API/worker 不得与会 `TRUNCATE` 的 fixture 共享验收库；
   跑 runtime contract 前先停止 acceptance API/worker，或改用独占数据库。

## 6. 验收顺序与通过标准

1. **基础运行时**：migration、`/ok`、`/ready`、`/info`、`/metrics` 全部成功。
2. **官方客户端**：Python 与 JavaScript SDK 分别完成 assistants、threads、runs、Store
   和 thread stream 生命周期。
3. **真实模型**：`chat` graph 通过 DeepSeek 完成一次小输入的 token stream，持久化
   最终 state/history，且不泄露凭据。
4. **高级图语义**：`subgraph` 断言 namespace/path 与 v3 event；`hitl` 断言
   interrupt、resume 和幂等重复 resume。
5. **恢复**：运行途中重启 API、停止 worker 并启动 replacement worker、重启 Redis；
   断言最终状态、retry_count、lease 和 SSE replay 正确。
6. **鉴权**：开发匿名路径与生产 delegation JWT 路径分开验证，包括跨 tenant 404 和
   custom route 共用 Principal。
7. **网络**：第二客户端中断连接后以 cursor 重连，断言只收到新事件且最终事件完整。

只有上述七类均通过，才能把“当前项目可用于完整真实链路”写为已验证事实。任何跳过
的外部模型、MCP、鉴权或网络场景都必须在报告中标为未覆盖，而不是算作通过。

## 7. 文档与图谱同步规则

在针对当前 commit 的 Store/thread stream 回归通过后，更新：

- `docs/compatibility-profile.md`：Store 标为已实现并验证的 Extended API；
- `docs/production-freeze-decisions.md`：保留原冻结决策，增加后续实现状态；
- `docs/production-decisions.md`：增加已验证证据链接。

历史 release notes 不做追写。文档修改完成后执行 `graphify --update`，只更新
`graphify-out/` 生成物，并核对报告中的 commit、节点数和核心连接是否反映新文档。

## 8. 依赖升级后的自动闭环

当前仓库已经有这条链路的基础：

```text
修改 LangGraph 依赖/uv.lock
        ↓
手动触发 Compatibility Upgrade workflow
        ↓
固定版本官方 langgraph dev + GraphHarbor 双服务
        ↓
OpenAPI/HTTP/SSE 差分 + Python/JavaScript SDK 契约
        ↓
PostgreSQL/Redis/worker 回归
        ↓
更新兼容矩阵后才允许发布
```

当前已经落地一条可审计的验收闭环：

1. **固定验收 fixture（已完成）**：`tests/acceptance_app/` 中的 graph、
   `langgraph.json`、依赖和固定输入已纳入仓库，每次运行使用同一组
   `basic/chat/subgraph/hitl/tool`，并额外覆盖 LangChain Agent 与 Deep Agent。
2. **统一结果文件（已完成）**：`tests/acceptance_app/run_acceptance.py` 产出
   `artifacts/compatibility-result.json`，每个能力记录 `status`、测试入口、依赖版本、
   结构化证据和脱敏失败摘要。
3. **能力到文档的映射（已完成）**：结果文件使用稳定的能力 ID，并由
   `docs/acceptance-capability-map.json` 逐项映射到测试入口、源码模块、文档段落和矩阵字段；
   `scripts/check_acceptance_mapping.py` 在本地和 CI 中校验路径与未知能力，不能只写一段
   “测试通过”。固定版本 P0 双端结果另由 `artifacts/official-langgraph-dev-p0-comparison.json`
   保存，缺少该基线时不得宣称对照完成。
4. **失败即生成待办（已完成）**：`scripts/compare_compatibility_results.py` 及验收
   runner 会生成 `artifacts/compatibility-diff.json` 和
   `artifacts/compatibility-followups.md`，区分代码失败、版本变化和外部依赖阻塞。
5. **更新门禁（已完成基础门禁）**：结果、映射和待办均已生成；只有所有必需能力为
   `passed`，并且人工确认排除项变化，才允许把新版本写入兼容矩阵。自动化不得自行扩大
   Core 或删除排除项。兼容矩阵的发布更新仍保留人工确认，不把它伪装成全自动发布。
6. **真实模型单独门禁（已完成本地手动层）**：DeepSeek smoke 使用本机
   手动命令触发，默认不在普通 CI 中运行，避免泄露凭据和不可控费用；结果仍写入同一
   结果 schema，并标出 provider/model/时间/费用不可知等元数据。
7. **升级前后对照（脚本已完成，历史基线未建立）**：
   `scripts/compare_compatibility_results.py` 保留上一版本结果后可生成能力级 diff，Agent 可以直接
   得出“新增失败、行为变化、需要同步的文档段落和需要补的测试”。

### 仍需讨论的设计点

| 议题 | 推荐默认值 | 原因 |
|---|---|---|
| fixture 放置 | `tests/acceptance_app/` | 和平台代码隔离，官方 SDK 可直接复用 |
| fixture 是否发布 | 不发布，仅测试资产 | 避免把验收图误当产品 API |
| DeepSeek 触发 | 手动 workflow + 本地命令 | 控制费用和密钥暴露 |
| 官方对照版本 | 当前 lock 中固定依赖启动的 `langgraph dev` 服务（`langgraph-api` compatibility spike） | 差分必须可复现，不能追浮动 main |
| Redis 验收 | 与 PostgreSQL 同时作为必需服务 | 完整链路依赖队列、Pub/Sub、cancel、replay |
| MCP 验收 | 外部 client 与 GraphHarbor inbound `/mcp/` 分开记录 | Streamable HTTP 已接入；生产认证和远端故障仍需门禁证据 |
| 文档更新权限 | 结果全绿后人工/Agent 显式确认 | 防止测试误判后自动扩大兼容声明 |
| 报告保存位置 | CI artifact，必要时提交版本化摘要 | 日志便于诊断，摘要便于跨版本比较 |

### 目前可以确认的答案

现有方案已经可以通过固定 fixture、真实 Agent 调用、统一结果文件、严格失败门禁和
固定版本 `langgraph dev` 双端差分发现大部分依赖升级回归，并生成待办清单。它还
不能自动修改兼容矩阵，也未建立完整官方四象限历史基线；这些仍需人工确认。

## 9. 三个核心缺口的补法

### 9.1 缺口一：固定验收 graph

目标是让每次依赖升级都运行同一组输入和同一组断言，避免测试结果被业务代码
变化、随机 prompt 或临时环境污染。

当前实际目录：

```text
tests/acceptance_app/
├── langgraph.json
├── graphs.py
├── run_acceptance.py
└── README.md
```

设计约束：

- graph 只测试 Agent Server/runtime 能力，不包含产品业务逻辑；
- `basic`、`subgraph`、`hitl` 使用确定性实现，允许官方服务和 GraphHarbor 做严格
  输出对比；
- `chat` 使用 OpenAI-compatible DeepSeek proxy，仅比较结构化不变量和事件类型，
  不比较自然语言逐字结果；
- `tool` 使用固定工具 schema，断言工具名称、参数、调用次数和结果状态；
- 每个 scenario 固定输入、stream mode、超时、重试策略和预期终态；
- fixture 不发布到 PyPI，不成为 GraphHarbor 的公共 API；
- fixture 自身版本随测试契约变更递增，依赖升级时不能静默修改旧 scenario。

通过标准不是“返回 200”，而是逐字段断言：run/thread/assistant ID 关系、SSE
event type、sequence、cursor、namespace、interrupt payload、tool call、最终
state、history、replay 和终态状态。

### 9.2 缺口二：统一结果产物

所有测试入口都必须写同一份能力结果 schema，而不是让每套测试自行打印日志。
建议产物：

```text
artifacts/
├── compatibility-result.json
├── compatibility-diff.json
├── compatibility-followups.md
└── logs/
    ├── official.log
    ├── graphharbor.log
    └── worker.log
```

每个能力结果至少包含：

```json
{
  "capability_id": "thread_stream_replay",
  "status": "passed",
  "tier": "deterministic_protocol",
  "tests": ["python_sdk", "javascript_sdk", "sse_reconnect"],
  "commands": ["pytest ...", "node ..."],
  "versions": {
    "graphharbor_commit": "5eea73f",
    "langgraph": "1.2.11",
    "langgraph_sdk": "0.4.3",
    "official_agent_server": "0.13.0"
  },
  "evidence": {
    "assertions": ["cursor_returns_only_new_events", "terminal_event_present"],
    "log_files": ["logs/graphharbor.log"]
  },
  "failure": null,
  "document_refs": [
    "docs/compatibility-profile.md#3.3-v2-普通远程流",
    "docs/compatibility-matrix.json#official-protocol-status"
  ]
}
```

状态固定为：

```text
passed
failed
blocked_external_dependency
not_run
informational
```

`blocked_external_dependency` 不得当作通过；普通 CI 不运行真实 DeepSeek 时必须
明确写 `not_run`，而不是跳过且不留痕迹。结果文件还应记录脱敏后的模型/provider
信息，绝不记录 API key、JWT 或完整敏感 prompt。

### 9.3 缺口三：版本前后 diff 和文档同步清单

依赖升级不能只比较“新 GraphHarbor vs 新官方服务”，必须保存四个快照：

```text
旧官方服务 ─────┐       ┌───── 新官方服务
                ├─官方行为变化─┤
旧 GraphHarbor ─┘       └───── 新 GraphHarbor
```

至少生成四类 diff：

| 对比 | 用途 |
|---|---|
| 旧 GraphHarbor vs 旧官方 | 当前已存在的适配差异 |
| 新 GraphHarbor vs 新官方 | 升级后是否仍兼容 |
| 旧官方 vs 新官方 | 官方版本自身行为变化 |
| 旧 GraphHarbor vs 新 GraphHarbor | GraphHarbor 本次升级引入的变化 |

diff 以能力 ID 为单位，不以日志文本为单位。分类规则：

```text
官方行为变化       → 更新兼容基线/版本映射
GraphHarbor 回归   → 修复代码并阻塞矩阵更新
适配缺失           → 生成代码任务和对应测试任务
文档过期           → 生成文档同步任务
外部依赖缺失       → 标记 blocked，不改变兼容声明
随机模型差异       → 只保留结构化不变量失败，不把文本差异判为协议回归
```

文档同步清单必须由映射表生成：

```text
capability_id
  → test files / commands
  → source modules
  → OpenSpec capability
  → docs section
  → compatibility-matrix field
```

例如 `store_api` 失败时，报告必须明确指向 Store handler、Store SDK 契约测试、
`compatibility-profile.md` 的 Extended 声明和矩阵状态，而不是只写“协议差异”。

自动化可以生成 patch 建议或 OpenSpec 任务，但不能自动：

- 扩大 Core 范围；
- 删除 `compatibility-exclusions.json` 条目；
- 把 `blocked`/`not_run` 改成 `passed`；
- 更新发布版本或推送包。

## 10. 推荐实施顺序

按最小风险拆四步：

1. **固定 fixture 和能力 ID（已完成）**：所有当前验收图有固定测试入口。
2. **统一结果 schema（已完成）**：REST、SDK、SSE 和真实 Agent 结果汇总到
   `compatibility-result.json`。
3. **升级对照和待办（已完成）**：可保存基线并生成
   `compatibility-diff.json`、`compatibility-followups.md`；
   `scripts/compare_compatibility_quadrants.py` 已接入四象限 capability-level 比较，
   但仍必须由升级流程提供真实的旧/新官方与旧/新 GraphHarbor 快照，仓库当前没有伪造的
   历史快照。
4. **文档门禁（映射校验已完成，矩阵更新仍需人工确认）**：映射脚本会阻止结果中出现
   未登记能力或失效路径；只有必需能力全绿且人工确认排除项变化，才更新矩阵和兼容声明。
   真实 DeepSeek、MCP、跨网络 SSE 作为手动生产验收层，不拖慢普通 PR；缺少外部证据时
   必须保持 `blocked_external_dependency`/`not_run`，不能改成 `passed`。

这样做的结果是：依赖升级后，系统不只是告诉我们“失败了”，而是能回答：

```text
哪个能力变了？
是官方变化还是 GraphHarbor 回归？
需要改哪段代码？
需要补哪条测试？
哪些文档和兼容矩阵字段必须同步？
哪些外部依赖只是暂时没跑？
```

## 11. 本次真实链路验收结果（2026-08-28）

已在本机 PostgreSQL `graphharbor_acceptance`、Redis DB 15 上启动 GraphHarbor API
和 worker，使用固定 `tests/acceptance_app/langgraph.json`，通过官方 Python SDK、
REST/SSE 和 JavaScript SDK 完成验收。API 启动完成后再启动 worker；并发首次创建
PostgreSQL checkpointer 会触发 `checkpoint_migrations` 类型冲突，启动编排必须遵循
该顺序。

最终结果保存在 [`artifacts/compatibility-result.json`](../artifacts/compatibility-result.json)：

| 能力 | 结果 | 真实证据 |
|---|---|---|
| runtime health | passed | `/ok`、`/ready`、`/info`、`/metrics` 均返回 200 |
| 基础 run 与 PostgreSQL state/history | passed | 官方 Python SDK stream、终态 success、state/history 可读 |
| 子图 namespace v3 | passed | typed lifecycle event 和 namespace 均存在 |
| HITL interrupt/resume | passed | interrupt id、resume、重复 resume 返回相同 run |
| tool 与 Store 生命周期 | passed | `multiply(3,4)`、Store put/get/search/list/delete 均验证 |
| 五个 acceptance P0 | passed | LangGraph supervisor/handoff、LangChain test-case/personal assistant、Deep Agent 均执行；后三者使用真实 DeepSeek 工具循环 |
| MCP 双方向 | passed | 外部 MCP client call 与 GraphHarbor `/mcp/` discovery/call 均通过 |
| SSE replay/本地多阶段 fixture | passed | `Last-Event-ID=1` 只返回后续事件；本机多阶段 stream 终态正确 |
| 固定 `langgraph dev` 双端对照 | passed | Core protocol pair 和五个 P0 pair 均为 `difference_count=0` |

这证明 GraphHarbor 的本地完整主链路、五个 acceptance P0 fixture 和本地 MCP transport
可用，但不等于外部业务仓库的 P0 graph、生产 JWT/tenant MCP 或跨主机 SSE 已验收。尚未
保存上一版本 snapshot 时，版本 diff 不能作为门禁通过：脚本默认以非零退出，只有显式
`--allow-missing-baseline` 才生成 informational 报告。
后续升级可执行：

```bash
uv run python scripts/compare_compatibility_results.py \\
  --baseline artifacts/compatibility-result.previous.json \\
  --current artifacts/compatibility-result.json
```

该脚本只按 `capability_id` 比较状态和版本，生成能力级变化清单，不把日志文本差异
误判成协议回归。

本轮另有一份固定 `langgraph dev` 成对执行结果：
[`artifacts/official-langgraph-dev-comparison.json`](../artifacts/official-langgraph-dev-comparison.json)。
结果为 `passed`，`difference_count=0`。它验证的是 GraphHarbor 与固定
`langgraph dev` 在健康端点、OpenAPI 路径/方法、assistant/thread/Store 生命周期、
thread-scoped stream 和最小 run SSE 场景上的结构化协议一致性，不是自然语言逐字相等。

统一 runner 的默认门禁只接受 `passed`。`blocked_external_dependency` 与 `not_run`
默认返回非零退出码；只有显式 `--allow-incomplete` 才是诊断模式。兼容性比较脚本在
缺少 baseline 时同样默认非零，只有显式 `--allow-missing-baseline` 才输出
informational，避免把未完成的升级验证误报为成功。

在专用本地 PostgreSQL `graphharbor_acceptance` 与 Redis DB 15 上运行仓库全量测试后，
结果为 `95 passed, 15 skipped`。跳过项是需要外部服务的 live 集成测试；其余 runtime、
队列、持久化、Store、worker、SSE 和认证契约均已执行。测试默认值仍保留 CI 的
`postgres:postgres@.../langgraph`，本机运行应显式设置 `DATABASE_URI` 指向专用测试库。

## 12. LangChain 与 Deep Agents fixture 完整性评估

当前 `tests/acceptance_app/` 已覆盖 LangGraph StateGraph、checkpoint、子图、HITL、
LangChain Core tool、LangChain `create_agent`、Deep Agents `create_deep_agent` 和真实
DeepSeek provider。两条 agent fixture 均在同一 GraphHarbor API/worker、PostgreSQL
checkpoint 和 Redis transport 上执行，不是离线 mock：

| 层次 | 当前状态 | 原因 |
|---|---|---|
| LangGraph | 已覆盖 | fixture 使用 `StateGraph`、子图、`interrupt` 和 v2/v3 stream |
| LangChain Core | 已覆盖 | `langchain_core.tools.tool` 定义并执行 `lookup_fact` |
| LangChain Agent | 已覆盖 | `langchain.create_agent` + `ChatOpenAI` + 真实 tool loop |
| Deep Agents | 已覆盖 | `deepagents.create_deep_agent` + `ChatOpenAI` + 真实 tool loop |
| 真实 provider | 已覆盖 | 现有 `chat` 通过 DeepSeek proxy 的原始 OpenAI-compatible SSE |

两条 fixture 使用 acceptance-only 依赖组（`langchain==1.3.17`、
`langchain-openai==1.6.0`、`deepagents==0.7.9`），不进入 GraphHarbor 生产发布依赖。
provider 不可用时 runner 会标记 `blocked_external_dependency`；本次依赖已安装，
真实 DeepSeek 验收全部通过，能力 ID、版本、断言已写入
`artifacts/compatibility-result.json`。

## 13. 复杂 P0、MCP 与跨网络 SSE 验收实现

`tests/acceptance_app/p0_graphs.py` 现已提供五个复杂验收 graph：

1. `assistant_supervisor`：LangGraph `Send` fan-out/map-reduce 和聚合。
2. `customer_support_handoff`：路由、billing/technical handoff、HITL 和重复 resume。
3. `test_case_agent`：LangChain `create_agent`、需求工具和验证工具 loop。
4. `personal_assistant`：LangChain `create_agent`、偏好读取和副作用前提下的日程提案。
5. `deepagent_demo`：Deep Agents `TodoListMiddleware`、subagent delegation 和总结。

它们注册在同一 `langgraph.json`，通过 `--with-p0` 接入统一结果 schema；supervisor
和 handoff 的图语义确定性可严格断言，后三个真实模型 loop 只比较结构化事实，不能把
模型自然语言差异当协议回归。`test_case_agent` 与 `personal_assistant` 对必需 tool 的
名称、顺序和次数作精确断言；`deepagent_demo` 将连续 `write_todos` 进度更新归一化，
要求 `write_todos -> task -> write_todos` 的委派阶段顺序，并拒绝未声明工具。`task`
是 Deep Agents 向 `fact_researcher` subagent 委派的唯一入口，主 Agent 不直接持有
`research_fact`。结果工件只保存结构化 projection/tool trace，不保存模型自然语言全文。
本轮本地运行五个 P0 fixture 全部 `passed`，并与固定
`langgraph dev` 逐个真实执行后得到 `difference_count=0`；统一 runner 共 `21/21 passed`（含 JavaScript SDK）。

`mcp_agent` 验证 Agent 内部作为 MCP client 连接外部 server；
`mcp_server.py` 使用 MCP Streamable HTTP fixture 验证 discovery、schema 和 tool call。
GraphHarbor 自身 `/mcp/` 现在也挂载了 stateless Streamable HTTP transport，按注册 graph
暴露同名 MCP tool；`--with-mcp` 还会从 GraphHarbor `/mcp/` 做 discovery 和 call。
legacy SSE 和 stdio transport 仍明确不在本实现范围内。

`run_network_sse.py` 提供第二客户端断开/`Last-Event-ID` 重连 harness，必须对独立远端
URL 执行才算跨网络证据；`--with-network-sse` 仅是本地多阶段 stream fixture，不得冒充
跨网络验收。当前本机 loopback 运行已验证脚本时序和协议路径，但跨主机/故障代理仍是
未完成的外部门禁，不能标记为 `passed`。loopback harness 已通过断开/重连和 cursor
断点检查和终态 values 投影，但只有把 `--cross-network-sse-url` 指向第二台受控主机
或故障代理后，结果才可作为跨网络证据。

本次还修复了两处闭环缺口：worker 序列化支持 LangChain `model_dump(mode="json")`，
并在 checkpoint 只有 `__pregel_tasks` 时回退到已持久化的 `threads.values`，因此
DeepAgent 的真实消息不会再出现“run success 但 state 为空”的假失败。

## 14. In-process Streaming 完整兼容边界

基于锁定的 `langgraph==1.2.11`，worker 现在通过一次
`graph.astream(..., version="v2")` 捕获并持久化全部标准 StreamPart 模式：
`values`、`updates`、`messages`、`custom`、`checkpoints`、`tasks`、`debug`。
每个事件保留 `type` 对应的 `method`、`ns` 对应的 `namespace`、`data` 和
`interrupts`；子图 namespace、LangChain 消息及其 metadata 也通过 JSON-safe
序列化保留。新增 `streaming_all_modes` fixture 和执行器测试逐项验证了这些模式。

这意味着 GraphHarbor 的远程 run SSE/Protocol stream 对上述原始模式提供可重放的
等价事件投影，且能被官方 SDK 消费。锁定版本的 in-process `RunStream` 投影则由用户
图内的 LangGraph 直接提供，并已按下表验证：

| 文档投影或模式 | 已验证行为 | GraphHarbor 网络承诺 |
|---|---|---|
| raw `stream` / `ProtocolEvent` | `type`、`method`、`seq`、namespace、payload；同步和异步消费 | 七个标准 v2 mode 以 durable typed event 重放 |
| `stream.values` / `stream.output` | 状态快照与最终状态 | `values` durable event |
| `stream.messages` | content-block 消息流与 `.text.get()` 最终文本 | `messages` durable event，不保留 Python 对象身份 |
| `stream.subgraphs` / `stream.lifecycle` | 子图 handle、path、started/completed lifecycle | namespace 与 lifecycle typed event |
| `stream.interrupted` / `stream.interrupts` | checkpoint 驱动的 HITL interrupt | `values` event 的 interrupts + run interrupted 状态 |
| `stream.extensions` | 调用方 transformer 创建的命名 projection；本轮 `audit` 真实验证 | 不承诺把 Python projection 对象序列化为 HTTP |
| `interleave()` | `values` 与 extension channel 的单消费者到达顺序 | 不适用，属于 Python 进程内消费 API |

v3 会按已注册 transformer 的 `required_stream_modes` 选择原始 v2 输入；因此
`updates`、`custom`、`checkpoints`、`tasks`、`debug` 的 in-process 派生 projection
由调用方 transformer 负责声明。GraphHarbor 不伪造固定的 `extensions` HTTP API，也
不承诺自定义 transformer 生成的 `custom:<name>` Python channel 可跨进程重建；由
`get_stream_writer()` 写入的标准 `custom` mode 则会被完整持久化和重放。

完整声明的证据入口：

- `libs/langgraph-runtime-pg/tests/test_public_runtime.py::test_executor_captures_all_v2_stream_parts`
- `libs/langgraph-runtime-pg/tests/test_public_runtime.py::test_langgraph_v3_inprocess_projections_and_interleave`
- `libs/langgraph-runtime-pg/tests/test_public_runtime.py::test_langgraph_v3_inprocess_subgraphs_and_interrupts`
- `libs/langgraph-runtime-pg/tests/test_production_contract.py::test_run_sse_v3_preserves_every_standard_stream_channel`
- `tests/acceptance_app/run_acceptance.py` 的 `inprocess_streaming_all_modes`
