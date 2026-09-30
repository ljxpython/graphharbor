# 0.13.0.post30：保留真实执行步数限制

状态：2026-09-15两包已发布PyPI，远端四产物hash与下表一致；平台已安装并重启到post30。

post29平台失败验收发现：请求config.recursion_limit=1仍能完成多节点任务。ProductionWorker重建RunnableConfig时遗漏顶层recursion_limit，工厂和执行器实际使用默认值。

修复 `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/production_worker.py:ProductionWorker.run_once`，按既有assistant→thread→run优先级传递明确设置的recursion_limit。由原生LangGraph处理步数上限／GraphRecursionError，不添加业务策略、任意配置透传或执行器。未设置的请求保持原默认。

`libs/langgraph-runtime-pg/tests/test_production_contract.py:test_worker_preserves_recursion_limit`用真实PG／Worker和原生两节点图验证3组覆盖关系、错误／成功与唯一终态。全量 **162 passed、18 skipped，86.98s**；ruff源码／scripts、68文件格式检查、Worker mypy、版本锁检查通过。

实际wheel在独立Python3.11环境启动API31396／Worker：HTTP嵌套／多中断／子图过滤／回放／心跳矩阵、SDK消息／工具／失败／中断通过。步数限制真实HTTP：thread `c40dfb78-f38c-4087-bbe0-6a476e44c460`，Run `addf701e-69d6-47a7-b641-cb7dda5c0cd8`，持久error且v3失败回放含Recursion limit。原始矩阵 `artifacts/v3-matrix-post30.json`。

心跳测试原先等待2秒，依赖验收环境将心跳缩短；干净环境默认15秒导致断言失败，现将慢图等待改为20秒以覆盖默认心跳，不改生产参数。

发布产物校验：

| 产物 | SHA256 |
|---|---|
| graphharbor wheel | 065c9c2121c7c2efbf9531ca8b17fd0becd47dc2b4be9dcaa2db8bbe07837fba |
| graphharbor sdist | 64ce06bc5a1d5357d4d80315a275df35fc02276a5614fc9c32139c1731c1489d |
| graphharbor-runtime wheel | 03b74dffdb2422e0244859fdd919735783df415a5b81389fc41b3898896327a7 |
| graphharbor-runtime sdist | f35ce8f90c2710cdd8496593dc233ec9fd0fe6360de5c14f37b1ce7981b54324 |

继承 [post29兼容范围与已知差异](release-notes-0.13.0.post29.md)。默认仍v2；不宣称官方Run SSE与GraphHarbor typed v3等价。前端只交接，本版不代表浏览器或默认切换完成。post29保留原产物，不覆盖重传。
