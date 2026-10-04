# GraphHarbor 0.13.0.post38

`graphharbor` 与 `graphharbor-runtime` 锁步发布。

- Worker 收到停机请求时，使用 LangGraph `RunControl` 在 superstep 边界 drain 并保存可恢复 checkpoint。
- 安全 drain 后，同一个 Run 进入待执行队列，由新 Worker 空输入续跑，避免重复执行已完成节点。
- 用户取消、运行超时与停机宽限期兜底保持原有语义；SSE 不增加公开事件类型。

使用方无需修改图定义。升级两个包到同一版本；回滚旧 Worker 前先处理带内部续跑标记的 Run。
