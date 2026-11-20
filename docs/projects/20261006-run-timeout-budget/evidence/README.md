# 使用方正式包证据

2026-10-07从平台专项实际生成产物原样复制，复用取消专项已经发布的 `graphharbor=graphharbor-runtime=0.13.0.post42`，未重复上传。发布四产物SHA256在 [取消专项清单](../../20261007-worker-cancel-propagation/evidence/release-artifacts.json)，两版本PyPI metadata已复查一致。

- [平台HTTP原始日志](run-budget-pypi-post42-http-e2e-r2.log)：12组退出0，包含新PID `11475 -> 12285` / attempt H=30到20，以及终态/租约/公开脱敏/文件/checkpoint断言。保留SSE断开时连接取消诊断。
- [匹配回退JSON](run-budget-pypi-post42-rollback.json) / [日志](run-budget-pypi-post42-rollback-r6.log)：当前Runtime/post42 drain后暂停提交，旧Runtime/post41同Run从checkpoint继续；pending/历史/产物保留、普通/取消/HITL通过，无Schema迁移。
- [PyPI核对](pypi-post42-verification.json)：两个版本四产物SHA256与发布清单一致；[源码核对](pypi-source-match.json) 证明新冷安装的49个Python文件逐字等于当前双包源码。

使用方项目 `docs/projects/20261006-agent-run-timeout-governance/verification.md` 保有详细版本/测试/旧源码对照。平台自身锁定post42，SDK0.4.3/Python3.13.9正式包HTTP通过；API自身SDK0.4.2定向85项通过。平台全量仍有4项旧源码同样失败，未宣称全仓全绿；前端F01-F10和联合Final待同事，现役未升级。

本目录日志中的源码路径、隔离端口和测试预算为原始定位信息，无真实凭据。通用底座专项done不代表使用方前端done或全部LangGraph Server协议等价。
