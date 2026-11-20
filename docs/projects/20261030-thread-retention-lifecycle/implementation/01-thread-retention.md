# Thread 生命周期实现记录

改动日期：2026-10-30；对应 G02–G07。进度仅看 [tasks.md](../tasks.md)。既有 post41–45 未提交修改按 `.cache/retention-governance/baseline-sha256.json` 留存，本次不清理其他专项。

## 持久策略与公开接口

| 位置 | 关键变化与理由 |
|---|---|
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/retention.py:63` `validate_policy()/set_policy()/touch()` | 原 TTL 维护为空实现；现在校验有限正数分钟、fixed/idle，保存服务器活动和到期时间，显式touch才续期。普通更新不能伪造业务活动 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py`、`migrations/versions/014_thread_retention.py` | 新增策略/活动/到期/删除标记与独立任务。迁移不凭旧updated_at推导过期，任务不随Thread消失 |
| `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py` `Threads` | 既有授权/线程锁下安装策略、读取、touch和提交删除；墓碑阻止相同UUID重新创建 |
| `libs/langhost/src/langhost/core_api.py`、`server.py` | 增加retention/touch/deletion路由和OpenAPI，复用SDK Auth；官方ttl:delete映射fixed，自动keep_latest明确拒绝 |

关键前后关系：原 `TTL扫描 → 无动作`；现 `expires_at → request_deletion → 持久任务`。核心不加入平台30天、业务表名或Workspace路径。

## 删除事务与生命周期交接

`retention.py:142 request_deletion()` 与创建/提交/touch 共享线程锁，先标记删除再拒绝新工作。`_blocked()` 保护活跃Run、未确认Lease/停止，TTL额外保护enabled Cron；手动删除可清理绑定Cron。

`retention.py:240 _engine_delete()` 在同一PG事务内清理三张checkpoint表、Thread/Run/Cron、事件/重试/取消事实并推进任务。`checkpoint_mutations.py:343 prune_checkpoints()` 复用checkpointer锁；`checkpoint.py`、`run_store.py`、`ops.py` 对复制、prune、checkpoint写入和运行提交加入一致屏障。

`retention.py:277 advance_deletion()` 将远端hook从SQL事务中移出。状态 `before_delete → after_delete → completed` 独立保存，claim两分钟、单阶段hook30秒、失败指数重试至多一小时。任务保存必须兑现的hook配置；缺失或改名不得静默跳过。before失败仍保留引擎数据，after失败保留外部回收依据，重启可续跑。

原同步删除无法证明外部资源释放；现在应用hook必须确认。完成时按 `tombstone_metadata_keys` 精简metadata；pending保留完整交接资料。acceptance永久最小墓碑、webhook各自规则不被本次删除覆盖；旧completed墓碑不追溯修改。

## 维护与发布

- `retention.py:361 maintain_retention()`：有限候选、短事务、skip-locked/持久claim；默认只读，apply才写。`retention_loop()` 由 `production_worker.py` 启动，每轮结束后等待60秒，无独立TTL关闭开关。
- `libs/langhost/src/langhost/cli.py:551 retention_command()`：加载本配置/所属env和hook，默认limit100、最大1000，输出计数；不打印metadata。
- 两包 `pyproject.toml`、`uv.lock`：锁步post48；本地build wheel/sdist、正式依次上传runtime/CLI四产物，冷安装及60个源文件逐字节对账。发布后只新增验证脚本的旧包恢复探针，未修改已发布包源码。
- `scripts/verify_thread_retention.py`：正式HTTP/CLI、前后hook失败、重启/墓碑/防复活、停维护与`--rollback-cli`；旧post45被014 schema head门禁拒绝，恢复post48后任务继续。迁移拒绝破坏性downgrade，不能用降包恢复已删数据。

## 测试与影响

新增 `libs/langgraph-runtime-pg/tests/test_thread_retention.py`，覆盖 `test_preview_touch_and_fixed_expiry`、`test_atomic_delete_checkpoints_and_tombstone`、`test_pending_and_cron_protected`、`test_handoff_failure_missing_config_and_restart`、`test_touch_vs_mark_and_single_claim`、`test_additive_migration_preserves_history_and_refuses_destructive_rollback`、`test_http_retention_auth_and_get_not_touch`、`test_completed_tombstone_drops_payload_and_preserves_authorization`。既有REST/SDK/CLI/schema测试同步。

源码392passed/22skipped、独立Cron3passed；候选wheel23/sdist19passed；正式13项与使用方三服务/浏览器通过。首次Cron固定库冲突、修正后结果、静态门禁和四产物hash见 [verification.md](../verification.md) 与 [发布说明](../../../releases/0.13.0/release-notes-0.13.0.post48.md)。

影响：需要migration014和API/Worker双包一致。未做生产部署、Git提交或破坏性数据库回退；生命周期hook承担的应用语义由使用方验收。
