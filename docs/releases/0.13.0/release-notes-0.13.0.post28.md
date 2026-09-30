# 0.13.0.post28：保留原生子图 lifecycle

## 边界与改动

GraphHarbor 是通用 Agent Server。本版只修复 LangGraph v3 子图事件转发，不引入业务 Agent、技能、工作区或用量账本。

- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/graph_executor.py:invoke_graph`：原生子图 lifecycle 的外层 namespace 可为空，实际 scope 在 `params.data.namespace`。旧过滤器误删子图 started/completed；现在只过滤真正的根 lifecycle，根 Run 终态仍在提交后发出。
- `libs/langgraph-runtime-pg/tests/test_public_runtime.py:test_executor_preserves_subgraph_namespace_and_interrupts`：真实嵌套 StateGraph 验证子图 started/completed 不丢失，仍保留 interrupt。
- `libs/langgraph-runtime-pg/tests/test_production_contract.py:test_record_event_repairs_stale_sequence_counters`：补非零旧游标回归。此前尝试的“仅游标为0时修复”会造成重复序号，已撤回；`record_event` 生产逻辑与post27一致。

## 性能结论纠正

此前将 `MAX(sequence)` 推断成全表扫描及平方复杂度，没有依据。真实 `EXPLAIN` 为 `Limit → Index Only Scan Backward using ix_runtime_events_thread_seq`。旧post27在未升级时已完成真实双子图运行（客户端完整断言后在清理请求失败，共12.57秒）。先前300秒超时不能归因本次查询或宣称已被本版“优化解决”。保持事件持久化、取消检查、fanout及300秒预算；不增加批处理或后台队列。

## 验证

独立数据库 `graphharbor_p3_perf_20260915`，Redis DB14／专用测试前缀；不使用调用方业务数据库。

- 全量 pytest：151 passed、18 skipped，67.92s。跳过项包含13个live-server E2E、4个已迁到业务层的委托策略测试、1个调用方opt-in用例；不计通过。
- PostgreSQL production contract：50 passed、4 skipped，20.38s；非零旧游标新增回归已通过。
- `ruff check .`、CI范围 `ruff format --check libs`、mypy（36个源文件）、`uv lock --check`、`scripts/check_versions.py`：通过。
- 全仓库Markdown样例及既有acceptance脚本的格式检查有5个存量文件提示，未修改与本版无关文件；CI仅要求libs格式检查。

已构建并发布PyPI，两包锁步post28；下游冻结安装并重启API／Worker。显式v3真实双子图经平台HTTP回放通过（7.74秒），两个running／completed、cause调用ID、独立namespace、根success均验证；默认v2兼容通过（10.89秒）。未修改业务逻辑。

四份远端产物SHA256与本机构建一致：

- graphharbor wheel：`8b8c68925b7c325ea3964107e83fce5e2d15bead9212cfc98103f8216f15daa9`
- graphharbor sdist：`285e45bc021cfa9a6db1296b2f4927a0b8eadf64e2ffcaca615da83f183d004f`
- graphharbor-runtime wheel：`aaea143518ca4f9b4d46ae227508237296b69c454a2daa631a1a71b00e24cb67`
- graphharbor-runtime sdist：`49fe7c7fdd5a00374de93b36f2f8122f60ffc78b866217e80284e49337794115`
