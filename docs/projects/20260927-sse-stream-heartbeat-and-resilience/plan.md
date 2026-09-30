# SSE 事件流确定性心跳、流式弹性与高频鉴权治理技术方案

## 1. 深度病灶剖析与技术反思

### 1.1 病灶一：心跳假死（过滤事件打断超时计时器）

在 `protocol_api.py` 的 `protocol_event_stream`、`streaming.py` 的 `thread_stream` 和 `_run_sse` 中，心跳调度采用了如下脆弱结构：

```python
heartbeat = 15.0
while not_timeout:
    try:
        msg = await asyncio.wait_for(queue.get(), timeout=heartbeat)
    except TimeoutError:
        yield ": heartbeat\n\n"
        continue
    
    # 消息到达后的过滤检查：
    if seq in seen or not matches(msg):
        continue  # <- 灾难发生点！
    
    yield frame(msg)
```

**为什么这会导致下游 45 秒超时断开？**
1. Redis Pub/Sub 或内部消息队列中不仅包含面向当前客户端的数据，还充斥着：
   - 其它节点的调试事件；
   - 内部控制消息（如 lease 心跳广播）；
   - 历史回放中已发送的事件（`seq in seen`）；
   - 客户端没有订阅的频道（如客户端只要 `messages`，但后端广播了 `custom` 事件）。
2. 当这些消息到达时，`queue.get()` 立即唤醒，`asyncio.wait_for` 成功返回，**心跳 15 秒计时器立刻归零重置**！
3. 但在随后的匹配中，该消息由于不符合客户端过滤条件被 `continue` 跳过，**既没有发送任何业务数据帧，也没有进入 TimeoutError 发送心跳包**！
4. 如果下游业务有持续的微弱背景事件（例如每隔 5~10 秒产生一个被过滤的内部事件），心跳超时将**永远无法触发**！
5. 客户端在 45 秒内收不到任何字节（既无数据帧也无 `: heartbeat\n\n`），判定连接僵死，直接发起 `client_disconnect`！

### 1.2 病灶二：内存回放循环内部的高频同步鉴权

在历史回放阶段，代码逻辑如下：

```python
# 致命逻辑：在纯内存回放迭代中逐条鉴权
for wire in replay:
    if await _thread(request, thread_id) is None:  # <- 每一条都做鉴权 + 查库！
        return
    yield _frame(wire)
```

在平台生产环境中，GraphHarbor 配置的 `auth_handler` 会通过 HTTP 调用平台的内部校验端点（`/api/runtime/internal/thread-authorization`）。
当回放 50 条消息时，GraphHarbor 竟然在同步循环中发起 **50 次独立的网络 HTTP 请求**！不仅回放延迟从毫秒级被拉长到数秒，而且当下游网关收到突发并发连接时，内部鉴权接口瞬间被打满。

### 1.3 病灶三：长连接响应头缺少 Keep-Alive 标头

在 `protocol_event_stream` 中：
```python
headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
```
缺少了 `"Connection": "keep-alive"`。而在生产环境中，请求必须穿透 Ingress Controller、Kong 或 Nginx。某些代理层在检测到缺少显式 `keep-alive` 时，会采取默认的短连接处理策略，或者在空闲窗口内单方面切断 TCP 通道。

### 1.4 病灶四：队列监听器在早期异常时的资源泄漏

在 `protocol_event_stream` 中：
```python
queue = await manager.add_thread_stream(thread_id)
# 下面的逻辑不在外层 try...finally 内部：
watermark, replay = await _load_protocol_events(thread_id, since)
if since and since < watermark:
    ...
resumable = await _resumable_run_ids(...)  # 如果这里抛出 DB 瞬时连接错误
# queue 将永远残留在 stream_manager 中！
```

---

## 2. 核心架构与解决思路

### 2.1 确定性活动心跳模型（Activity-Based Heartbeat Scheduling）

引入“最后向客户端发送数据的时间戳” `last_sent_at`，并将超时等待时间动态化：

```text
当前时间 now = loop.time()
距离下次心跳剩余时间 remaining = max(0.1, heartbeat - (now - last_sent_at))
```

1. **动态等待：** 每次调用 `asyncio.wait_for(queue.get(), timeout=remaining)`，确保等待时间严格基于上次实际活动时间，而不是固定 15 秒。
2. **过滤补偿发射：** 如果 `queue.get()` 获取到了消息，但随后该消息被业务过滤条件丢弃（`continue`），在跳过前执行**补偿检查**：
   ```python
   if loop.time() - last_sent_at >= heartbeat:
       yield ": heartbeat\n\n"
       last_sent_at = loop.time()
   ```
   **效果：** 彻底杜绝无论队列内有多少被过滤的垃圾事件，客户端都能在不超过 `heartbeat` 秒的时间间隔内确定性收到心跳包！

### 2.2 三流协同治理（Aligning All 3 Streaming Endpoints）

将确定性心跳调度全面铺开到 GraphHarbor 承载的所有流式端点：
1. `libs/langhost/src/langhost/protocol_api.py` 的 `protocol_event_stream`（Agent Protocol 官方 SDK 规范）；
2. `libs/langhost/src/langhost/streaming.py` 的 `thread_stream`（Thread 级别标准 SSE 流）；
3. `libs/langhost/src/langhost/streaming.py` 的 `runs_stream` / `_run_sse`（Run 级别标准 SSE 流）。

### 2.3 鉴权削峰与连接级防抖（Connection-Scoped Auth Caching）

1. **进入流前严格校验：** 在流建立之初执行一次强鉴权；
2. **内存回放零鉴权：** 在加载完当前批次的内存数组后，由于回放耗时仅数毫秒，且回放数据就是属于当前已经过鉴权的 thread/run，彻底移除循环内部每帧调用 `_thread` / `_get_thread` 的愚蠢逻辑；
3. **心跳与实时事件鉴权防抖：** 在持续监听长连接期间，引入单连接内的短时时间戳防抖（如 10 秒）。在此窗口内，直接复用已验证的合法状态，避免心跳和过滤事件频繁向平台内部接口发送同步 HTTP 请求。

### 2.4 健全标准响应头与流队列生命周期闭环

1. **响应头标准规范：**
   所有 SSE 接口统一使用完整且健壮的标头：
   ```python
   headers = {
       "Cache-Control": "no-cache, no-transform",
       "Connection": "keep-alive",
       "X-Accel-Buffering": "no",
   }
   ```
2. **严格的异常保护：**
   `queue = await manager.add_thread_stream(...)` 后，紧跟 `try...finally` 块，确保包括 `_load_protocol_events`、`_resumable_run_ids` 在内的所有前置逻辑都在 `finally: await manager.remove_thread_stream(...)` 保护之下，消除一切潜在泄漏可能。

---

## 3. 具体修改代码与文件清单

### 文件 1：`libs/langhost/src/langhost/protocol_api.py`
- **函数：** `protocol_event_stream(request: Request)`
- **修改内容：**
  1. 将 `queue = await manager.add_thread_stream(thread_id)` 后的所有逻辑完整纳入 `try...finally` 结构；
  2. 声明 `last_sent_at = asyncio.get_running_loop().time()`；
  3. `while` 循环中使用 `remaining = max(0.1, heartbeat - (now - last_sent_at))`；
  4. 在 `seq <= since or seq in seen` 以及 `not _wire_matches(wire, body)` 这两个 `continue` 分支前，添加活动时间检查与心跳补偿补发；
  5. 响应头补充 `"Connection": "keep-alive"`。

### 文件 2：`libs/langhost/src/langhost/streaming.py`
- **函数：** `thread_stream(request: Request)`
- **修改内容：**
  1. 引入 `last_sent_at`；
  2. 动态计算等待剩余超时时间；
  3. 当 `rows` 为空或当前批次的所有行都被 `_thread_frame` 过滤（未产生任何输出）时，检查 `loop.time() - last_sent_at >= heartbeat`，若超时则补发 `: heartbeat\n\n`；
  4. 响应头补充统一的 `"Cache-Control": "no-cache, no-transform"`。
- **函数：** `_run_sse(...)`
- **修改内容：**
  1. 在实时事件监听循环中应用 `last_sent_at` 机制；
  2. 当 `live_envelope` 为空或未能生成任何有效 frame 时，增加补偿心跳发射；
  3. 移除回放循环中逐行重复的 `_run_snapshot` 查库鉴权。

### 文件 3：`libs/langhost/src/langhost/core_api.py`
- **修改内容：**
  - 为长连接鉴权提供短时辅助（或保证在长连接流内部复用已认证的 principal 上下文，避免无效重复开销）。

---

## 4. 边界与约束核对

- **通用运行时边界：** 心跳包格式遵循标准 SSE 规范（`: heartbeat\n\n`），不携带任何业务 trace 或特定模型厂商字段；
- **平台兼容性：** 平台在 Platform API 网关层虽然已做了外层兜底注入，但 GraphHarbor 本身发送标准心跳后，网关会因为检测到已有数据帧流经而自然静默，形成完美的双保险；
- **官方协议契约：** 保持与官方 LangGraph Agent Protocol 的事件帧顺序与序列号语义完全一致。
