# 验证

## 验证计划

G1 协议/配置；G2 真实 PG 原子事务及生命周期；G3 HTTP 安全/可靠投递；G4 Worker 与使用方跨服务、回归/容量/回滚；G5 正式产物冷安装。所有破坏性测试只操作本专项隔离库。

## Phase 验证记录

### G0 2026-10-09

已读取 CONTEXT/AGENTS/双包规范入口/相关 lessons，识别既有 dirty 文件。用户批准来自会话，未自行批准。正式双包 `0.13.0.post44` 已从 PyPI 核对并由使用方锁定。

### G2 2026-10-09

- 隔离 native PG/Redis：`scripts/run_isolated_tests.py .venv/bin/python -m pytest libs/langgraph-runtime-pg/tests libs/langhost/tests -m 'not e2e' -q --tb=short`：332 passed、8 skipped、13 deselected、2 failed，失败仅旧 cron 用例强制要求另一专用库。
- 按测试要求指定 `--database-name graphharbor_cron_probe_20261005 --redis-prefix graphharbor:cron-probe-20261005` 单独执行 `test_cron_dispatch_live.py`：3 passed，12.04s。
- 生命周期覆盖 finish/fail、rollback/delete、cancel fence、最终 reaper `awaiting_stop`、同 generation 停止确认、commit rollback；等待确认记录不会被 retention 删除。
- 双 Worker 首次启动暴露 schema advisory lock 与 concurrent index 的 snapshot 死锁；改用 autocommit 下短事务 `pg_try_advisory_lock`。真实并发 index 回归已在上述 332 passed 中通过。

### G3 2026-10-09

- 上述回归包含 sender lease 接管/旧 generation、503/错误 ACK、ACK 后 sender crash、重新签名及 key 轮换、24h deadline、死信重放。
- 网络契约验证 DNS 私有地址/重绑定拒绝、验证精确 IP、系统 CA 与 hostname、受管 3xx 不跟随、普通 profile 跨 host redirect 去敏感 headers。
- Ruff 全双包 check 通过；mypy 42 source files 无错误。G4 隔离引擎 broad 回归 `339 passed, 9 skipped, 13 deselected`，Python/JS webhook 定向 `16 passed`；容量、积压恢复和 post43/head011→post44/head012 回退证据已由使用方专项保存。

### G5 2026-10-09

- PyPI `graphharbor==0.13.0.post44` 与 `graphharbor-runtime==0.13.0.post44` wheel/sdist 四产物 hash 与发布清单一致；平台依赖锁核对、冷安装、CLI、迁移 head012、Ruff/mypy 通过。
- GraphHarbor 包本身的 G1-G3、PG/Redis 生命周期、网络安全和 dispatcher 证据齐全；未部署现役服务或执行生产迁移。
- 正式post44也包含既有metadata CAS路由/原子merge和migration011；site-packages直接导入的PG/HTTP CAS矩阵 `4 passed`，56.848s，JUnit `run-completion-post44-artifact-cas.xml` SHA256 `9b42233f6195390dd7f51413e6c622bd555d0a939265be1cdc28d52b2447a400`。发布事实已同步CHANGELOG/REST标准/健康表，其他专项状态仍以其任务清单为准。
- 本地收尾两仓diff --check通过；markdown-it核对本专项及CAS/取消发布补记共11份Markdown/15个相对链接，独立专项状态未改变。使用方22份JUnit统计/哈希、18个JSON和真实DTO导出校验通过；未将失败矩阵写为通过，前端/现役部署没有执行。

### G4 限制

- 使用方正式 post44 整链路多次在本机高负载或夹具超时条件下失败；这不回写为 GraphHarbor 自身测试通过。具体 JUnit、失败原因和重跑条件见使用方专项 `docs/projects/20261009-agent-production-capability-extension/verification.md` P3.3。
- 正式包最短completion链路 `1 passed`，449.393s；完整v6/v7分别 `1 failed`，853.363s/433.438s。native尾段 `1 failed`，563.100s：8定时场景及旧cron dry-run/重复apply/revert/删除抑制已执行，10s队列blocker等待超时，队列TTL/重启未完成。部分步骤不计整项通过，统计/哈希由使用方 `evidence/test-results.json`、`official-post44-tail.json` 保存。
- 本期测试使用本机initdb/postgres/redis-server、随机端口和隔离目录，没有Docker依赖。20:51本机load924.48/694.25/667.19；G4正式完整矩阵资源blocked，用户通知资源可用后继续native复验；不停止其他任务、不改生产规则凑通过。全部本期测试已退出，轻量文档与契约收尾可以独立完成。

## Final 验证记录

尚无本专项Final。GraphHarbor实现、隔离回归和发布完成；G4正式包完整矩阵因本机资源暂时blocked，本专项保持partial。使用方前端与浏览器验收不属于引擎专项范围，不作为GraphHarbor完成条件。
