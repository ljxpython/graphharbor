# Thread 固定目标取消 - 验证

> 2026-10-09发布事实补记：post44正式双包已包含本专项原语/migration011，冷安装/head和来源核验见[webhook专项G5](../20261009-run-completion-webhooks/verification.md)。本页post42/post43统计是历史Phase证据；使用方正式源接入仍未由该专项验收，保留blocked，不生成Final结论。

> 状态blocked，2026-10-07。源码、唯一post43四产物/发布前门禁与包版Docker证据完成；正式PyPI发布指令及使用方正式源接入待完成。Phase与Final分开，其他专项正式post42成果不作为本专项发布证据。

## 环境

使用本仓库HEAD `f9bb32e` 加本专项改动；工作树存在Worker/attempt/cron等其他专项成果，未覆盖。所有写测试经 `scripts/run_isolated_tests.py` 创建临时原生PG/Redis，无现役库写入。平台worktree HEAD `bf47991b` 加本专项改动，四图走实际组合根和合成模型，不调用付费供应商。

首轮同名post42候选仅为历史Phase证据。本轮唯一双包 `0.13.0.post43`、head `011_run_cancellations`，使用方Python3.13.9/LangGraph1.2.11/原SDK0.4.3；临时复制Runtime锁仅更新双包并冷安装140包，未使用GraphHarbor源码PYTHONPATH。平台仓库正式锁/现役仍为post41/head010/SDK0.4.3。

从PyPI下载正式post42 wheel并核实哈希后逐项对照：仅新增cancellation模块/迁移011，修改server路由、graph_executor身份传播和models；正式post42不包含本专项新能力。候选post43未上传；查询PyPI时最新双包仍post42，post43无文件。

## Phase 验证记录

### Task 1：固定目标

`test_thread_cancellation.py` 覆盖151目标分页、同key/ID冲突/重复读、新Run、20次claim/submit/cancel竞争，完整快照或整单回滚，未做活动列表截断。精确授权事件带服务端cancel_active或cancellation_receipt marker；使用方检查ID/hash/Thread并验证只读回执不能读Run/state。

### Task 2：停止证明

此前引擎取消/Worker/public-runtime定向48项通过。新增lease fence无退出事件场景返回confirmation_unavailable，补持久事件后才确认。平台真实长工具/模型等待、Showcase/DearFlow并行子Agent、local执行和Runtime重启通过；assistant_id/graph_id进入configurable，LangGraph Runtime重建身份保持。

### Task 3：容量与回退

新增竞争/容量集26 passed；容量1/10/100/500均完整取消和分页。500个pending目标约33.24秒，含分页5524 SQL；复用逐Run取消有线性成本，源码已标ponytail升级方向，未批准SLO，不宣称秒级。平台真实500目标引擎提交超过单次10秒等待，后台仍以同stop_id回读并收敛。

首轮同名post42候选双wheel/sdist构建与独立冷安装、import cancellation/RunCancellationRow、head011、CLI version通过。以下是历史候选wheel哈希，不是正式post42或当前post43产物：

```text
graphharbor: 26f6ed9f9a9d23629b9d6aba186a64a5dc95926b033c0fd904c92f71c1400e39
graphharbor-runtime: 20f03844b9973ed8889401706250601d02df96203a4b5a2468e4bb85c3244f06
```

使用方恢复7场景通过：候选HTTP、Runtime downgrade保留回执、Runtime再upgrade幂等、pg_dump/pg_restore、引擎downgrade保留Run取消意图、正式旧post41单Run cancel、引擎再次upgrade。引擎downgrade删除新增目标表，报告需要备份；不将已取消Run复原pending。仅隔离库验证。

首轮使用方真实HTTP15场景与Runtime25项通过，当时Docker未启动。本轮唯一post43包版16场景全部通过；Showcase/DearFlow实际命令开始后取消，容器移除、cleanup_confirmed持久回执及8秒后无延迟写入通过。真实Docker后端/PPTX另3项通过；未影响既有容器。Runtime Stop/inbox47 passed/1 skipped，API66 passed/376 subtests passed；1项既有fatal文案断言已在HEAD复现并留档，未改变范围外语义。

唯一post43四产物构建、Python3.11/3.13独立wheel导入、sdist独立安装、CLI/head011与twine check全部通过；完整SHA256如下：

| 文件 | SHA256 |
|---|---|
| graphharbor_runtime-0.13.0.post43-py3-none-any.whl | 96c16ccff6314855d5d65a38b8c8633ee3791deeb907f841810c87e334659b65 |
| graphharbor_runtime-0.13.0.post43.tar.gz | 2455bb803dbbdf8ab6688a07f932341ab85b1c86588a66cdad29112519606bb7 |
| graphharbor-0.13.0.post43-py3-none-any.whl | 8fcb0910829355df2582e5e88881c527e442ff5dc94a92de85b760d9e2911670 |
| graphharbor-0.13.0.post43.tar.gz | 53a87a10d3edbead97e8ffbd50d350ab38ba6fc37afd71a13b64b4070dfc7fdd |

源码双包锁步、uv.lock/check_versions/兼容矩阵已同步post43；uv lock --check、Ruff check/format（73文件）、mypy（39源文件）通过。Python3.11引擎主回归297 passed/8 skipped，Cron独立库3 passed；Python3.12生产契约124 passed/4 skipped；Python3.13冷安装wheel契约与固定取消150 passed/4 skipped。初跑迁移head旧断言和跨实例订阅recheck竞态已修正并复跑，Cron使用独立库/Redis前缀；失败过程见使用方verification。skip为既有可选验收，不记为通过。

详尽回执、四产物哈希、7条迁移/备份/旧post41恢复和正式接入步骤保存在使用方 `docs/projects/20261007-agent-run-cancellation/{verification.md,release-handoff.md,evidence/}`。原件 `/tmp/agent-stop-post43-acceptance-5543.json`、`/tmp/agent-stop-post43-migrations-5543.json`、`/tmp/thread-stop-post43-engine-final-5543.xml`、`/tmp/thread-stop-post43-cron-final-5543.xml`、`/tmp/thread-stop-post43-python312-final-5543.xml`、`/tmp/thread-stop-post43-python313-final-5543.xml`；临时原件不承诺长期保留。

最新本专项6个Python文件Ruff check/format/compile通过，新增回执路由一处换行已修正；两仓diff检查通过，平台35个Python路径静态检查通过。33份项目/活标准定向检查新增内容通过，3条使用方FEATURES旧断链已HEAD对照，未修改；正式同版本PyPI下载未被当成本专项新接口证明。

唯一post43文档同步后复核check_versions、compatibility baseline、uv lock --check与两仓diff --check通过。30份项目/活标准文档、291个相对链接、21个闭合代码块及9份JSON无新增错误，3条使用方FEATURES基线断链与HEAD一致；twine四产物均PASSED，SHA256复核一致。正式PyPI上传已向用户请求明确发布指令，仍未执行。

## 复跑

从本仓库根运行，下列变量指向独立测试解释器和使用方worktree；shell文档不带rtk。

```bash
"$GRAPH_HARBOR_PYTHON" scripts/run_isolated_tests.py \
"$GRAPH_HARBOR_PYTHON" -m pytest \
libs/langgraph-runtime-pg/tests/test_thread_cancellation.py -q -s

PYTHONPATH="$PLATFORM_ROOT/apps/runtime-service/src" \
STOP_ROLLBACK_PYTHON="$OLD_RUNTIME_PYTHON" \
"$GRAPH_HARBOR_PYTHON" scripts/run_isolated_tests.py \
"$CANDIDATE_PYTHON" "$PLATFORM_ROOT/scripts/verify_stop_migrations.py"
```

不得读取现役DSN或运行非隔离 `scripts/test.sh`，不凭候选版本号推断正式发布。

## Final 验证记录

未开始。本专项唯一post43正式PyPI上传待明确指令，之后使用方正式源锁接入与包版复验。发布前证据完成，整体blocked；现役部署另行指定，不属于当前开发验收门禁。未提交、上传或升级现役，不提前标记整体done。
