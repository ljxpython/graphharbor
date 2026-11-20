# 0.13.0.post49：Checkpoint 存储治理

2026-11-21正式发布graphharbor-runtime、graphharbor双包wheel/sdist。四产物SHA256和正式源冷安装见[发布证据](../projects/20261120-checkpoint-storage-governance/implementation/release-post49.json)。

Run边界改为head引用和受影响行的首次before-image；结果投影采用同事务JSON Patch反向差量。显式prune保护Delta祖先，批量回收无引用blob；未开启活跃历史自动裁剪。修复更快claim时SSE首帧attempt错误加一。

精确组合：LangGraph1.2.24、checkpoint4.3.2、checkpoint-postgres3.2.1、SDK0.4.14、CLI0.4.31；migration head为015_checkpoint_journal。生产安装不需要可选上游langgraph-api。

升级须停止并排空API/Worker，备份到独立恢复目标，执行一次性015转换后再启动匹配双包。无法证明历史归属时转换失败并保留旧事务；不以旧包接管新schema回退，不支持破坏性downgrade。详见[运行手册](../runbooks/production-runbook.md)。

正式源42 passed/1 deselected，另有HTTP/Python/JS取消24场景通过；候选和容量结果见[项目验收](../projects/20261120-checkpoint-storage-governance/verification.md)。严格官方逐字段对照仍有既有差异；不宣称整个Agent状态或永久原文存储有固定上限。本次未执行Git提交、推送、tag或现役部署。
