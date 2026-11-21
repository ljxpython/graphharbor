# 验证记录

2026-09-24启动，执行者Codex。当前状态为部分完成（partial）：工程与2026-11-21追加差分/容量/有界并发/正式交付已验证，负责人最终验收仍待人工。以下早期环境与结果保留历史范围。

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

## 2026-11-21追加阶段验证（Phase）

通过原生一次性PG/Redis runner运行新增脚本 `scripts/verify_checkpoint_protocol.py`。官方langgraph-api0.13.0/runtime-inmem0.33.0与正式PyPI post50各42场景完成，分别覆盖jobs0/1。所有响应低于500；foreign单取消403/404，非法参数400/422，跨owner混合批次后另一用户线程与Run逐项不变。脚本总预算300秒，子迁移60秒，就绪60秒，逐请求20秒；finally仅回收所属进程组，PG/Redis随runner退出。认证只用fixture专用签名密钥。

两端存在13条状态差异：重复interrupt为官方404/本端202或200；running重复rollback的非等待请求可为404/202；混合批量官方422/本端204；keep_latest官方in-memory422/本端PG200；混合prune官方404/本端200。它们是实测差异，不能概括为完全等价；既有terminal rollback扩展和pending删除时序说明保留。完整脱敏行见[逐例矩阵](../20261120-checkpoint-storage-governance/implementation/protocol-matrix-post50.json)。

新增确定性8 Worker并发：全部在节点屏障内运行，8条prune均409，8条外部update_state均CheckpointConflict，竞争claim无可领取；半数rollback恢复逐项checkpoint/write基线，另一半正常完成，全部终态后prune成功且state不变。单项1 passed/5.18秒，完整安全套件18 passed/17.11秒、2条上游Beta warning；不是生产吞吐量承诺。

六组最高10,000条/16KiB固定负载已有真实旧失败和新预算数据；全量基线被journal-v1替代，继续测试已撤掉的旧设计没有意义。新维护预算、备份恢复、正式post50四产物与独立安装、平台真实进程交接见[新项目验收](../20261120-checkpoint-storage-governance/verification.md)。不将其测试数转记成旧运行结果。

harness初次被路径越界守卫拒绝，随后因非JSON取消响应解析失败，再因认证fixture缺签名密钥导致create500；均修夹具，不放宽产品路径/认证守卫。最终同步与原生async子进程两个版本矩阵均各42场景完成，最终脚本Ruff通过。

## 等待人工的最终验收

项目维护者须审阅上述官方协议差异与发布/恢复限制，确认原安全专项验收。AI不代签；生产网络分区/Redis完全不可用及任意高并发容量未在本轮验证，不用有界fixture替代生产演练。当前没有剩余已知产品修复代码等待实施。
