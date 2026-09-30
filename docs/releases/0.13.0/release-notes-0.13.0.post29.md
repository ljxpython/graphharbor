# 0.13.0.post29：生命周期作用域与并行中断恢复

GraphHarbor保持通用Agent Server边界。本版修复线程lifecycle根running、graph_name、error，提升真实子scope用于订阅过滤；Run SSE关闭子图时也过滤payload中的目标scope。原生cause保留，新持久事件timestamp稳定回放。根终态仍以已提交事务为准。

线程命令成功返回type=success供官方SDK消费。input.respond使用官方中断ID→回答映射，修复并行中断无法独立恢复的问题，恢复继承来源Run的流版本；没有新增数据库表或业务执行器。

发布前验证：全量159 passed/18 skipped（67.49秒）；原生参考图3 passed；ruff源码/scripts与格式通过。官方0.13.0与GraphHarbor真实对照涵盖嵌套、捕获子错误、并行中断、SDK消息／子图／工具／HITL；实际wheel独立API/Worker也通过HTTP矩阵及JS SDK。SIGKILL后lease回收恢复，持久化仅一个completed。事务提交失败不会发布completed。

v2/v3交替各20次无错误，确定性图总时长中位381/389ms；v3返回完整typed事件，字节311/19688，不能宣称性能或流量优化。默认仍v2。

## 明确的兼容边界

- 官方0.13.0 Run SSE传version=v3仍是旧形状；GraphHarbor Run SSE typed v3是扩展。官方线程Protocol v2本身支持lifecycle，不能更名冒充同一版本。
- 严格lifecycle样本差异26项，包含既有status/reason/output扩展、子终态data.namespace及额外原生子interrupted。核心五种event与实际graph_name/error/cause已对照；不承诺逐字节等价。
- 官方HITL源Run的持久status可能为success，GraphHarbor保留interrupted；客户端以lifecycle/interrupt事实判断审批。
- SDK1.9.28在双方都将同一工具ID产出两个handle，Server原始事件只有一对；客户端应回归其锁版本，不应删除Server事件。官方参考订阅未收到checkpoints/custom，GraphHarbor的这两种持久事件已实际消费通过。
- 前端／Vue验收由调用方独立交付；本版不代表前端已切换或所有官方托管功能完整兼容。

产物与最终发布结果见`v3-post29-candidate.md`；平台记录见ai-agent-platform的`docs/projects/20260915-graphharbor-v3-alignment/implementation/03-backend-closeout.md`。
