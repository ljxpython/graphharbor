# Thread metadata CAS 验证

> 2026-10-09发布事实补记：post44正式双包已含CAS/原子merge，实际PG/HTTP `4 passed`，56.848s，来源/四产物/hash见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。下文post43记录是旧正式基线；C03使用方正式源接入验收未由webhook专项替代，整体partial/Final未执行状态不变。

## Phase 验证记录

### C01 数据库与HTTP（2026-10-09）

命令：`PYTHONPATH=libs/langhost/src:libs/langgraph-runtime-pg/src .venv/bin/python -m pytest libs/langhost/tests/test_thread_metadata_cas.py -q --tb=short`。

4 passed，最新36.35s。独立临时原生PostgreSQL：两种rename顺序、同值rename清条件、两个session屏障并发唯一winner、8个无关键并发merge、missing/null、不可见filters、空/非法条件、HTTP404/409/422、ttl-only和minimal204通过。测试结束关闭自建PG，不操作现有库。

### C02 候选与使用方（2026-10-09）

双包offline wheel/sdist构建；使用方冻结锁136依赖cold install后，仅替换两个本地wheel，不用引擎源码PYTHONPATH。临时环境 `/tmp/f07-title-candidate-20261009/venv-v2` 的direct_url指向v2候选，两模块从该venv加载。

| v2产物 | SHA256 |
|---|---|
| graphharbor wheel | ae4ae5564558a488c47e80c7498fe109c50f0684e4a5c57a7222d069378e8660 |
| graphharbor-runtime wheel | 22b87a8afa4abedc3f5bcc3b3d88d3fc1460c6e8094c036c547bae72f28aafde |
| graphharbor sdist | 41553f24a3bc3fd9f30173d76208e0fa80e1c7ff40fd66561cef9922aa0a7258 |
| graphharbor-runtime sdist | db9c94bdd29585d617a2e0209950afad26d7a7f3d2b6584d97f7013d42b39573 |

使用方真实HTTP/Worker/PG/Redis与受管模型：8个fixture场景和百炼qwen-plus真实主Run/标题CAS/刷新/state与SSE隔离，2 passed，91.14s；后续ACL撤权场景见使用方记录。fixture两个并发标题仅一个applied，改名/gate/model撤销不写，CAS提交丢响应503后GET对账且不再生成。候选链路只证明专项行为，不证明同工作树其他专项都通过。

最新使用方9场景fixture含真实平台ACL中途撤销：1 passed、1 deselected，79.28s；agent8/title9。撤权403、规则title/pending不变；真实模型成功证据仍保留上一轮。Ruff helper/test/core_api/server/ops检查及两仓diff --check通过。

文档收尾：源码/候选完成与C03正式发布blocked在README、tasks和CONTEXT对齐。用已安装的markdown-it解析平台16份、引擎10份文档，共128个本地链接目标均存在；未检查外链/片段锚点。两仓diff --check通过。

### 正式来源核对（2026-10-09）

PyPI `0.13.0.post43` 官方CLI wheel SHA256 `8fcb0910829355df2582e5e88881c527e442ff5dc94a92de85b760d9e2911670`，runtime wheel `96c16ccff6314855d5d65a38b8c8633ee3791deeb907f841810c87e334659b65`；下载hash与PyPI digest一致。官方CLI含cancel-active，不含metadata/cas；官方runtime无thread_metadata.py。本地同名候选绝不代表正式能力。C03未获发布指令，正式新版本和使用方正式源接入blocked。

## Final 验证记录

未执行。C03正式发布/接入未完成，当前`partial`；源码/CAS候选可交接，不能报正式包done。
