# 验证记录

2026-09-24，执行者 Codex。总体状态 **partial**：核心实现和本地真实存储验证完成，完整官方差分、规模性能及发布预演尚缺。

## 环境与隔离

专用可丢弃 PostgreSQL 数据库（名称保存在本机 `/tmp/graphharbor-checkpoint-safety-db`），Redis 15 与独立前缀。未使用默认项目数据运行清空 fixture。测试使用 LangGraph 1.2.11、checkpoint-postgres 3.1.2；不调用真实模型。

## 已执行结果

| 验证 | 结果 |
| --- | --- |
| 专项 + production_contract + persistence_contract + rest_contract + official_sdk_contract | 85 passed、4 skipped、2 Beta warnings，54.02s |
| 后续增强：专项 + persistence（新进程恢复和迁移往返） | 14 passed，27.13s |
| 最终数据库时钟改动后专项 | 10 passed，15.86s |
| ruff：runtime/host 源码及相关测试 | 通过 |
| mypy：checkpoint、checkpoint_mutations、run_store、production_worker、ops、core_api、server | 7 files 通过 |
| git diff --check | 通过 |

4 项 skip 均为已有测试，原因是 DelegationJWTValidator/RuntimePolicy/JWKSCache 已迁往业务层。不是本功能通过证据。Beta warnings 来自上游 v3 streaming。

## 专项覆盖

1. A→B→rollback B：原始 checkpoints/writes JSON 完全相同、thread 投影恢复、迟到 writer 拒绝、C 继续成功。
2. 真实 HTTP auth middleware：跨 tenant 混合/重复/不存在 ID，其他租户完全不变，计数只含授权资源；非法输入 422。
3. 真实 ProductionWorker 阻塞节点取消：wait=true 返回前停止并恢复。
4. pending 回滚保留历史。
5. DeltaChannel + 子图多 namespace：prune 后 state 不变，继续运行正确。
6. 历史依赖返回 409 且不改数据；批量逆序回滚恢复基线。
7. 持久回滚意图禁止 prune/manual writes/claim；过期后独立 Python 进程启池清理，恢复历史并可继续 claim。
8. 在 checkpoint restore INSERT 注入异常：整个事务回滚，run 与当前历史保留；重试成功。
9. HITL interrupt 的 tasks/pending writes 保留，prune 后 Command(resume=42) 成功。
10. 旧 profile 使用删除授权事件，未授权 thread 不变。

迁移测试使用隔离 schema，先 006，再 007，再降到 006 后升到 007；Alembic schema check 通过。此测试不等于生产数据迁移预演。

## 官方固定版本实机探针

独立临时工作目录、localhost:2197、官方 `langgraph-api 0.13.0` + `langgraph-runtime-inmem 0.33.0`，已停止该临时服务。

| 场景 | 官方观察 | GraphHarbor |
| --- | --- | --- |
| 已完成 B 的 rollback | 404，state 保持 B | 有安全基线且无后继时 200，恢复 A；扩展差异 |
| keep_latest | 422，in-memory 不支持 | PG 实现 200；后端扩展差异 |
| after_seconds=60 pending rollback wait=true | 200 `{}`，紧随 GET 仍 200 | 本地 pending 测试删除后 GET 404；时序差异待进一步对照 |

没有声称完全 API 等价；未将在线文档当作所有返回码的实测证据。官方探针不是完整自动差分套件。

## 未完成证据

- 完整官方 running/batch/repeated/auth/input 矩阵。
- 大历史存储与并发压力、网络分区/Redis 全部不可用时的部署演练。
- 生产环境负责人评审、API/worker 同版本发布预演。

上述为 partial，不伪记 done；代码可供评审，当前不建议据此宣称完全兼容或直接全量发布。
