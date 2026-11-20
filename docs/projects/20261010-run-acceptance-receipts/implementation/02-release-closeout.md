# 接受回执正式发布与收尾

2026-10-10；对应 V03。任务进度唯一事实源为 [tasks.md](../tasks.md)，项目为 done、13/13。

双包 `0.13.0.post45` 已按 runtime → graphharbor 使用本地令牌直接发布。四产物 SHA256 见 [唯一发布集](artifacts-final.json) 与 [独立PyPI核验](pypi-formal.json)，令牌只注入上传进程，未保存到仓库或证据。

正式 wheel/sdist 各独立冷安装、CLI/site-packages/head 核验及5组真实 API/Worker 场景通过。使用方正式锁、Dockerfile 断言和安装来源已核验；notify/Stop/撤权发布后联合验证通过，完成模型调用分别1/0/0，B01已解除。具体命令与独立 Final 证据见 [verification.md](../verification.md#65-正式-pypi-与发布后联合-final2026-10-10)。

V03 收尾 Phase 检查版本锁步、两仓库依赖锁、compatibility baseline、Ruff及文档/13完成卡/13条Phase/四处状态一致性，输出 [final-closeout.json](final-closeout.json)。未重复累计 Final 与 Phase 用例数量。

对应 REST/SSE 规范已 active、last_verified 为 2026-10-10，健康表已同步。经验仅提案：进程 harness 改 cwd 时使用绝对 CLI 路径，并核验安装来源和 schema head；待用户确认后再写 lessons。

未提交 Git、未部署现役、未宣称平台 T08 剩余矩阵或 T10/F12 全栈 Final 完成。永久墓碑随 key 线性增长；恢复到接受前备份会丢防重事实。接受回执只证明持久接受，不证明停止、通知完成或外部副作用 exactly-once；fsync=off 隔离测试不证明断电耐久。
