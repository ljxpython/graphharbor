# GraphHarbor 0.13.0.post42

2026-10-07 双包锁步发布：

- [graphharbor-runtime](https://pypi.org/project/graphharbor-runtime/0.13.0.post42/)
- [graphharbor](https://pypi.org/project/graphharbor/0.13.0.post42/)

## 运行行为

Worker通过共享Redis控制订阅即时接收取消，注册/重连后复查control key与PG。执行、stream、受管子任务与资源退出前维持续租和Thread保护；之后按owner/generation提交持久停止与lease释放。rollback保存停止/恢复回执，失败可重试；lease到期只fence，不伪报执行停止，也不重新执行已取消Run。

`wait=false`空body 202只受理；`wait=true`成功200晚于停止和持久提交，无法确认/超时503。SSE/join使用同一停止依据。confirmed body null与锁定官方pending {}、running __error__有差异，官方两种SDK均丢弃cancel body。已发送外部请求、工具副作用与任意同步线程不承诺可撤回。

同版发布包含Worker attempt修订：每次claim重新计时，硬限timeout、模型/provider超时error，DB瞬时故障有界恢复，正常checkpoint handoff不耗故障attempt。后续使用方已锁定正式包，12组超时HTTP/SSE、真实新PID接管与匹配源码回退通过，见 [attempt专项](../../projects/20261006-run-timeout-budget/verification.md)；平台前端与联合Final另验。

## 验证与产物

发布前源码270 passed、专用Cron3 passed；定向129 passed/4 skipped；官方/候选各24项HTTP/Python/JS、冷安装24项、回退/kill/混合12项；平台默认取消及实际子图9项通过。性能24样本并发8，零heartbeat tick/失败，控制queue和受管task退出归零。skip不计通过，Phase与Final分开记录。

四个PyPI产物SHA-256与本地一致，49个wheel Python文件与源码逐字一致。Python3.13新环境从PyPI安装双包，CLI/79项依赖检查通过，生产未安装langgraph-api；发布包锁定SDK0.4.3与安装版SDK0.4.6的HTTP/Python/JS矩阵各24项、回退/kill/混合12项通过。Final见[专项验证](../../projects/20261007-worker-cancel-propagation/verification.md)，产物哈希见[清单](../../projects/20261007-worker-cancel-propagation/evidence/release-artifacts.json)。

取消专项done；全部Task完成后Final源码270 passed、专用Cron3 passed、发布包矩阵24项及静态/文档门禁通过。未把Phase、skip或其他使用方未完成项记为本轮Final通过。

## 升级与回退

双包API/Worker须锁步。先停止新提交并核对active/pending/rollback，等待drain或取消清理确认后切换；回退post41也必须先完成此对账。新增内部事件payload/receipt，无Schema迁移。混合版本不能提供新版停止保证：新API/旧Worker无依据时503，旧API仍可能提前200。

本次仅发布包；未提交/推送/tag、未升级或重启现役平台。前端消费停止中/已停止/确认超时由使用方同事完成，不新增取消配置。
