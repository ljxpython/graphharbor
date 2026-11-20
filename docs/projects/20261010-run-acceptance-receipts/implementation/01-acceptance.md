# Run 接受回执实施事实

2026-10-10 初始实施阶段记录；G01–G06 与 U 接线的设计与 Phase 证据。最终进度只看 tasks.md，发布/Final 收尾见 02-release-closeout.md 与 verification.md。

## 事务与回执

独立 POST/GET 使用 raw UTF-8 SHA256；认证 grant 固定 scope、credential、Thread、key_hash 和摘要。SDK Auth 新路径缺 handler 默认拒绝。key advisory lock 先于 Thread 锁，Run、签名和 accepted ledger 同事务，只有首次提交注册 after-commit enqueue；结构化 Thread busy 才写 rejected。GET 与重放零业务写 SQL。

migration013 独立固定列/约束/索引，不引用未来 ORM，无 Run/Thread FK。012 旧 Run 快照升级后未变；downgrade 明确拒绝删除 ledger。7 天详情期限只从维护首次观察终态或删除开始，活跃 Run 不清理；永久墓碑继续封住原 key。维护分开使用到期/open 两个索引，每轮最多1000，SQL 过滤活跃行后 limit，再短锁复核 Run。

## Phase 证据与已修正失败

- 引擎相关初始回归：47 passed、3 skipped，55.33s。
- 新 HTTP/PG 边界：31 passed、1 failed，132.84s；失败是迁移 SQL 种子缺必填 name，补齐后迁移用例 1 passed，34.34s。
- 包含连续 GET SQL 监听、commit/sign 回滚、两 Thread 同 key、缺 ledger 完整性故障、10万行索引/双维护/活跃重排。
- 使用方相关回归：117 passed、116 subtests，103.79s；新增 final bytes-before-send、全部普通4xx unknown、reconcile 无模型/POST、委托绑定与 DTO 校验。
- Runtime 鉴权/执行 guard：36 passed，34.26s；Stop suppressed/另一 run_id 不覆盖新 PG 用例单独 1 passed，181.97s。
- 全 Runtime repository 一次运行 19 passed/5 skipped/2 failed，1647.68s；失败为既有45秒 lease/初始 due 时间边界，尚未完成复核，不计全量通过。
- source process：5组真实进程通过，证据 source-process.json。最初代理干扰回环，已设置 trust_env=False 后重跑通过。

这些是初始 Phase 记录，不替代随后独立执行的 Final；全量、冷安装混合、正式联合依赖和 PyPI 发布结果见 verification.md。
