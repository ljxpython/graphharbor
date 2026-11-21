# 0.13.0.post50：检查点日志与流事件修复

2026-11-21锁步正式发布双包wheel/sdist。内部Worker领取Run时先建立journal baseline，线程状态PATCH不再污染缓存请求JSON；跨topic、跨Run的同值事件ID不再互相误去重。未改变Schema015、通用引擎边界或平台历史保留政策。

源码84e9021的远端CI44840687787七项通过，包含live-server E2E、完整上游Python SDK与JavaScript SDK契约。发布前本地源码405通过、9跳过、13项live单独验证；完整脚本32/13/55项和独立Cron3项通过。无诊断RemoteGraph v3连续30轮各五项通过，这一固定夹具不构成任意并发竞态永不发生的保证。

四个正式产物与候选SHA256逐项相同，runtime51个、CLI11个Python源码逐字节一致，未包含私有配置或使用方源码。PyPI版本JSON和索引存在刷新时间差，未重复上传；通过正式simple索引下载验真后，从其官方wheel地址在全新Python3.13环境无缓存安装。实际导入来自site-packages，双包post50和head015一致；关键真实PG/SDK/CLI41通过，需可选上游langgraph-api的一项由源码与远端套件验证，未向生产包增加该依赖。正式安装另完成HTTP/Python/JS取消24场景。

精确版本、四产物哈希与来源见[发布证据](../projects/20261120-checkpoint-storage-governance/implementation/release-post50.json)。014→015仍须停写、备份与一次性转换，失败恢复到独立目标并使用匹配包；不得用旧包接管015或覆盖带新写入的现役库。发包不等于生产部署，使用方实际API/Worker与业务链路由关联项目继续验收。
