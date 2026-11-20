# 0.13.0.post45 — Run接受回执与只读对账

2026-10-10，双包已使用本地令牌直接发布PyPI；令牌只注入上传进程，未写入源码/证据。

创建Run的ACK丢失时，使用方可以按原key和最终请求字节摘要读取持久接受事实，即使Worker尚未启动也能找到原run_id。独立POST/GET `/threads/{thread_id}/runs/acceptance`、原子Run/ledger、可信固定grant和SDK授权过滤、持久忙碌拒绝及永久防重墓碑随migration013发布。

POST成功与持久拒绝均HTTP200，通过result判定；冲突/过期POST409，GET无证据或到期unknown。固定read grant只允许原回执GET。active Run详情不过期，reaper首次观察终态/删除后默认保留7天，之后墓碑永久封key。013明确拒绝删除接受事实的downgrade。

- [graphharbor 0.13.0.post45](https://pypi.org/project/graphharbor/0.13.0.post45/)
- [graphharbor-runtime 0.13.0.post45](https://pypi.org/project/graphharbor-runtime/0.13.0.post45/)
- 四产物：[唯一发布hash](../../projects/20261010-run-acceptance-receipts/implementation/artifacts-final.json)、[上传记录](../../projects/20261010-run-acceptance-receipts/implementation/pypi-upload.json)。
- [Final验证](../../projects/20261010-run-acceptance-receipts/verification.md)记录源码、两版SDK、候选/正式源冷安装与真实API/Worker故障链路。
- 正式PyPI wheel/sdist各5组真实进程通过；使用方正式锁接入和notify/Stop/撤权发布后联合通过，接受回执专项13/13、done，B01解除。原平台全范围Final及新Linux应用镜像另验。

这是GraphHarbor可靠性扩展。永久墓碑存储随key线性增长；备份回滚到接受前会丢防重事实；接受不等于执行停止或通知完成。现役未部署，未执行git commit/push/tag。
