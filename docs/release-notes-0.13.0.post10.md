# GraphHarbor 0.13.0.post10

## 新增

- worker 会在单次图执行中持久化 `values`、`updates`、`messages`、`custom`、
  `checkpoints`、`tasks` 与 `debug` 全部标准 LangGraph v2 stream mode，并将其重放为
  typed remote event。
- 增加锁定 `langgraph==1.2.11` 的 v3 in-process `RunStream` 验证，覆盖 raw event、
  values、messages、output、subgraphs、lifecycle、interrupt、extensions 与 interleave。
- 修复多个 worker 同时领取同一 thread 的 pending run 时的 PostgreSQL 竞态；领取过程会
  锁定 thread 并在锁内复查运行状态，避免违反单 thread 单 running run 约束。

## 兼容边界

- Python `RunStream` 和自定义 transformer projection 是 LangGraph 的进程内 API；
  GraphHarbor 不将其作为 HTTP 对象重建。标准 `custom` mode 会持久化并重放。

## 验证

- runtime production contract：连续两次 `37 passed`；队列并发领取回归覆盖 16 路竞争。
- streaming 目标契约：`11 passed`，并完成 Ruff、Mypy、能力映射与 OpenSpec 严格校验。
