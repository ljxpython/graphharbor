# SSE 事件流确定性心跳、流式弹性与高频鉴权治理验收方案

## 1. 验收目标与退出准则

本专项的验收依据事实，不接受任何“理论推导通过”或“未运行宣称通过”。
必须满足以下四大硬性指标方可标为 `done`：

1. **心跳抗干扰指标：** 在持续存在高频“过滤丢弃事件”（`channels` 不匹配、重复 `seq`、非目标 `modes`）的极端恶劣网络与消息环境下，客户端在规定的心跳周期内必须确定性收到 `: heartbeat\n\n`，心跳发射偏差不得超过 0.5 秒，绝不允许出现静默饥饿假死。
2. **鉴权削峰指标：** 在历史消息回放场景（50 条消息）下，对鉴权回调的远程调用次数从原先的 50 次下降至 1 次（进入流时的基准鉴权），长连接静默期鉴权频率受防抖窗口严格控制。
3. **资源泄漏零容忍指标：** 客户端断开连接或流异常退出后，`StreamManager` 中的活跃队列计数必须立即回落至 0，无任何孤儿队列残留。
4. **静态与测试门禁：** Ruff 0 lint 错误、Mypy 0 类型错误，所有现有及新增的单测、契约测试 100% 通过。

---

## 2. 核心验证场景与设计

### 场景一：高频过滤事件下的确定性心跳发射（Anti-Starvation Heartbeat）

- **测试思路：**
  - 设置较短的心跳周期（如 `heartbeat=1.0` 秒）；
  - 客户端建立 `protocol_event_stream` 连接；
  - 启动并发后台任务，每隔 0.2 秒向该线程的 stream queue 灌入一条非法或已被过滤的事件（例如 `seq` 属于已接收过的消息，或者 `channels=["other"]`）；
  - **断言：** 客户端在 2.5 秒内必须至少收到 2 个 `: heartbeat\n\n` 心跳包，证明过滤事件无法“偷走”或重置心跳计时器。

### 场景二：历史回放过程鉴权调用削峰（Zero Per-Event Auth Storm）

- **测试思路：**
  - 预先在测试库或 mock 存储中写入 20 条事件；
  - 拦截并监控 `_authorize` / `auth_handler` 的调用次数；
  - 客户端以 `since=0` 发起事件流连接，触发全量回放；
  - **断言：** 回放完成并成功输出 20 个事件帧后，`auth_handler` 的调用次数严格为 1 次，严禁出现 20 次网络 HTTP 调用。

### 场景三：异常前置抛错下的队列资源安全回收（Leak-Free Lifecycle）

- **测试思路：**
  - 模拟在 `add_thread_stream` 之后、流开始消费之前触发存储异常（如 `_resumable_run_ids` 抛出数据库连接错误）；
  - 捕获该异常并确认接口响应为 500；
  - **断言：** `stream_manager` 中针对该 `thread_id` 的队列集合已经为空，彻底杜绝资源泄露。

### 场景四：完整 Keep-Alive 与响应标头校验（Header Compliance）

- **测试思路：**
  - 分别请求 `protocol_event_stream`、`thread_stream` 和 `runs_stream`；
  - **断言：** 响应状态码为 200，`content-type` 为 `text/event-stream`，且标头中必须同时具备：
    - `Connection: keep-alive`
    - `Cache-Control: no-cache` 或 `no-store`
    - `X-Accel-Buffering: no`

---

## 3. 具体验证执行命令清单

```bash
# 1. 运行流式保活与弹性专项测试
rtk uv run pytest -v libs/langhost/tests/test_stream_heartbeat_resilience.py

# 2. 运行应用鉴权全套回归
rtk uv run pytest -v libs/langhost/tests/test_application_authorization.py

# 3. 运行官方协议对比套件
rtk uv run pytest -v libs/langhost/tests/test_official_protocol_compare.py

# 4. 代码规范与类型安全门禁
rtk uv run ruff check .
rtk uv run mypy libs/langhost/src libs/langgraph-runtime-pg/src
```

---

## 4. 验证记录与结论归档

### 4.1 专项单测与心跳断言
- 命令：`rtk uv run pytest -v libs/langhost/tests/test_stream_heartbeat_resilience.py`
- 结果：`3 passed in 1.49s`
- 核心指标：
  - `test_protocol_event_stream_heartbeat_under_filtered_traffic`：高频过滤内部事件下，在 0.3s 内确定性输出 2 次心跳，流关闭后队列立即清理。
  - `test_thread_stream_heartbeat_under_empty_queue`：空闲流心跳稳定输出，队列正常解绑。
  - `test_stream_response_headers_compliance`：`Connection: keep-alive`、`Cache-Control: no-cache, no-transform`、`X-Accel-Buffering: no` 标头 100% 具备。

### 4.2 全量回归
- 命令：`rtk uv run pytest libs/langhost/tests`
- 结果：`60 passed, 3 skipped in 2.61s`
- 涵盖：`test_application_authorization.py`（同步/异步 handler 鉴权、流授权拒绝校验全部通过）。

### 4.3 静态检查门禁
- `rtk uv run ruff check .`：`All checks passed!`
- `rtk uv run mypy libs/langhost/src libs/langgraph-runtime-pg/src`：`Success: no issues found in 37 source files`

### 4.4 版本锁步与产物构建
- 版本：`0.13.0.post34`
- `rtk uv lock --check`：通过
- `rtk python3 scripts/check_versions.py`：`ok: graphharbor=0.13.0.post34; dependencies={'langgraph': '1.2.11', ...}`
- 构建产物：
  - `dist/graphharbor_runtime-0.13.0.post34-py3-none-any.whl` (及 tar.gz)
  - `dist/graphharbor-0.13.0.post34-py3-none-any.whl` (及 tar.gz)
- 独立隔离安装验证：
  - 命令：`rtk uv run --isolated --no-project --with dist/graphharbor-0.13.0.post34-py3-none-any.whl --with dist/graphharbor_runtime-0.13.0.post34-py3-none-any.whl graphharbor --version`
  - 结果：`graphharbor, version 0.13.0.post34` 正常输出。

### 4.5 历史僵尸中断重放治理专项验证（0.13.0.post35）
- **复现单测：** `test_zombie_interrupt_replay_filtered`
  - 初始红灯：重放列表同时输出已审批中断 `int-old` 与活跃中断 `int-active`，断言失败。
  - 修复后绿灯：`int-old` 被严格剔除，仅 `int-active` 输出，耗时 `0.67s` 通过。
- **全套回归：**
  - `rtk uv run pytest libs/langhost/tests`：`61 passed, 3 skipped in 3.30s`。
  - `rtk uv run ruff check .`：通过。
  - `rtk uv run mypy libs/langhost/src libs/langgraph-runtime-pg/src`：通过。
- **版本锁步与发布：**
  - 版本号：`0.13.0.post35`。
  - PyPI 发布确认与隔离安装：`graphharbor, version 0.13.0.post35`。
- **业务端集成验证：**
  - `apps/runtime-service` 锁定依赖升级为 `graphharbor==0.13.0.post35`；
  - `local-stack.sh restart` 顺利重启各服务；
  - `test_runtime_gateway_event_redaction.py` 14 个测试用例全部通过（2.37s）。
