# SSE 事件流确定性心跳、流式弹性与高频鉴权治理任务清单

## 任务执行原则

1. **没有预期结果与验证方式的任务不能标完成**；
2. **严禁编造测试结果**，所有验证必须在真实测试环境运行并记录确定性输出；
3. **改动最小化**，手术刀式修改，绝不随意动无关代码。

---

## 任务拆解与进度

### Phase 0: 缺陷复现与基线红绿灯

- [x] **T01: 编写“过滤事件吞噬心跳”确定性复现测试**
  - **位置：** `libs/langhost/tests/test_stream_heartbeat_resilience.py`
  - **内容：** 模拟客户端连接 `protocol_event_stream`，高频灌入过滤事件，断言心跳发射。
  - **实际验证：** 通过，用例 `test_protocol_event_stream_heartbeat_under_filtered_traffic` PASS (1.49s)。

- [x] **T02: 编写“空队列与标头合规”确定性测试**
  - **位置：** `libs/langhost/tests/test_stream_heartbeat_resilience.py`
  - **内容：** 验证 `thread_stream` 空闲心跳与所有 SSE 端点标准化响应标头。
  - **实际验证：** `test_thread_stream_heartbeat_under_empty_queue` 与 `test_stream_response_headers_compliance` 全部 PASS。

---

### Phase 1: 确定性心跳调度与流式弹性实施

- [x] **T11: 改造 `protocol_event_stream` 实现确定性活动心跳与生命周期保护**
  - **位置：** `libs/langhost/src/langhost/protocol_api.py`
  - **实际验证：** `last_sent_at` 调度、过滤分支补偿心跳、`try...finally` 队列清理及标头已就绪并通过专项测试。

- [x] **T12: 改造 `thread_stream` 实现确定性活动心跳**
  - **位置：** `libs/langhost/src/langhost/streaming.py`
  - **实际验证：** 确定性心跳及标头已落地，通过专项测试。

- [x] **T13: 改造 `_run_sse` 实现确定性活动心跳与内存回放优化**
  - **位置：** `libs/langhost/src/langhost/streaming.py`
  - **实际验证：** 移除了逐帧 DB 查库鉴权，增加了 Keep-Alive 标头与心跳补偿。

---

### Phase 2: 鉴权削峰与防抖实施

- [x] **T21: 实现长连接流生命周期内的轻量鉴权防抖与同步 handler 兼容**
  - **位置：** `libs/langhost/src/langhost/protocol_api.py`、`libs/langhost/src/langhost/streaming.py`、`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/authorization.py`
  - **实际验证：** 支持同步/异步 handler，10s 鉴权防抖窗口；`test_application_authorization.py` 全绿。

---

### Phase 3: 全量回归与压力验证

- [x] **T31: 运行所有流式与协议测试套件**
  - **验证命令：** `rtk uv run pytest libs/langhost/tests`
  - **实际验证：** 60 passed, 3 skipped in 2.61s。

- [x] **T32: 运行静态质量门禁检查**
  - **验证命令：** `rtk uv run ruff check .` 与 `rtk uv run mypy libs/langhost/src libs/langgraph-runtime-pg/src`
  - **实际验证：** ruff 0 错误，mypy 37 个源文件 0 错误。

---

### Phase 4: 文档收尾、版本锁步与发布准备

- [x] **T41: 更新项目文档与验证事实**
  - **位置：** `docs/projects/20260927-sse-stream-heartbeat-and-resilience/verification.md`
  - **实际验证：** 详细记录测试命令、耗时与产物检查。

- [x] **T42: 锁步升级版本与本地构建**
  - **版本号：** `0.13.0.post34`
  - **实际验证：** `uv lock --check` 通过，`check_versions.py` 通过，wheel 构建并在隔离环境安装验证成功。

---

### Phase 5: 历史僵尸中断事件重放治理（HITL 一致性根治）

- [x] **T51: 编写“历史已审批中断重放”复现红灯单测**
  - **位置：** `libs/langhost/tests/test_stream_heartbeat_resilience.py`
  - **用例：** `test_zombie_interrupt_replay_filtered`
  - **实际验证：** 成功捕获红灯：`int-old` 在重放中被错误下发导致断言失败。

- [x] **T52: 实施 SSE 重放活跃门禁与 Resume 旧 Run 状态终态化**
  - **位置：** `libs/langhost/src/langhost/protocol_api.py`
  - **改动点：**
    1. 在 `protocol_event_stream` 中引入 `active_interrupt_ids` 单一事实来源过滤，将已消费的 `input.requested` 从重放列表中物理剔除；
    2. 在 `protocol_commands` 的 `resume` 分支中，将前序旧 Run 的 `kwargs["stream_resumable"]` 显式置为 `False`。
  - **实际验证：** `test_zombie_interrupt_replay_filtered` 转全绿（0.67s），全套 61 个单测无回归。

- [x] **T53: 锁步升级版本 0.13.0.post35 并完成 PyPI 发布与业务端升级**
  - **版本号：** `0.13.0.post35`
  - **实际验证：** PyPI 成功发布并完成独立安装验证；`ai-agent-platform` 更新锁定并在服务栈重启后跑通 14 个网关测试。
