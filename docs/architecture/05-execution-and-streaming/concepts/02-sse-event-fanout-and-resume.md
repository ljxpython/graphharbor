# 02-真实流式通信：SSE 事件扇出、Last-Event-ID 重播与保活心跳

> **概念定位与核心价值**：本专篇深度解密 GraphHarbor 为高并发 Web 客户端提供丝滑“打字机（Typewriter）”体验的**工业级流式通讯架构**。详述如何通过 **PostgreSQL 事件游标补发与 Redis Pub/Sub 实时流双轨融合**，彻底攻克长连接网络抖动断流、反向代理 60 秒超时掐线、以及移动端断网重连无感续传（Last-Event-ID）的核心技术壁垒。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
小李坐地铁上班，手机上追看一部热门连载小说：
- 作者写得极快，每 2 秒更新一个字（大模型流式吐 Token）；
- 地铁突然驶入地下长隧道，手机信号直接跳到“无服务”（TCP 连接闪断）；
- 5 秒后，列车驶出隧道，手机重新连上 5G 信号；
- 如果小说软件写得极烂：页面直接弹出一个红叉“网络异常”，小李只能点刷新，小说又从第一章第一段重新开始滚动播放，小李气得想摔手机！
- **真正高级的阅读软件怎么做？** 小李手机里捏着一枚电子书签（`Last-Event-ID: 45`）。重连服务器时，手机向网关展示书签：“我刚才看到第 45 字了”。网关从数据库里秒级调出第 46、47、48 字补发过来，接着无缝切入作者正在写出的第 49 字实时流，小李阅读毫无阻碍！

### 2. 软件工程演进痛点
在搭建大语言模型执行服务端时，许多开发者仅仅用一个简单的异步生成器做 SSE 推送，生产上线后立刻遭到暴风雨般的事故打击：
1. **网络抖动推倒重来（Costly Abort & Re-run）**：大模型生成一段 2000 字的长文需要 30 秒。若客户端在第 25 秒发生 TCP 断开，由于服务端未解耦计算与推送，任务直接被 `CancelledError` 中断。用户重试，大模型重新推理，企业付出了双倍的昂贵 Token 账单；
2. **反向代理静默掐线（Proxy Idle Timeout）**：企业往往在应用层前置了 Nginx、Cloudflare 或 AWS ALB。当智能体调用一个耗时 40 秒的外部搜索或代码执行工具时，中间没有任何文本输出。反向代理检测到连接空闲超过 30~60 秒，直接向客户端返回 `504 Gateway Timeout` 并强制切断长连接；
3. **内存与连接泄漏（Socket & Queue Leaks）**：大量前端用户反复刷新页面，旧的 SSE 连接被关闭但服务端后台监听协程未被正确销毁，Redis 订阅队列不断堆积，最终引发系统 OOM 崩盘。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：直接绑定 HTTP 响应，无心跳、无重播、断网即死
async def naive_sse_stream(request: Request, agent_generator):
    # 致命伤 1：遇到大模型思考或工具等待，长时间不发字节，Nginx 直接掐线
    # 致命伤 2：客户端断开后无法续传，重连只能从第 0 个 Token 重新生成
    async for chunk in agent_generator:
        yield f"data: {chunk}\n\n"

# ✅ 生产级落地方案 (GraphHarbor Production)：双轨游标补发 + 自动注入保活心跳
async def production_thread_stream(request: Request, thread_id: UUID):
    cursor = parse_last_event_id(request.headers.get("Last-Event-ID"))
    # 核心防守 1：先查库回放断网期间落下的历史事件帧
    watermark, missed_rows = await fetch_events_since(thread_id, cursor)
    for row in missed_rows:
        yield format_sse_frame(row, event_id=f"{row.sequence}-0")
    
    # 核心防守 2：无缝接入 Redis 实时流，空闲期注入注释行保活心跳
    queue = await stream_manager.add_thread_stream(thread_id)
    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                yield format_sse_frame(msg, event_id=f"{msg.sequence}-0")
            except TimeoutError:
                yield ": heartbeat\n\n"  # 15s 保活心跳，彻底免疫 Nginx 掐线
    finally:
        await stream_manager.remove_thread_stream(thread_id, queue) # 严防泄漏
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 源码精准定位与调用链路
流式通讯系统在 [`libs/langhost/src/langhost/streaming.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langhost/src/langhost/streaming.py) 中由四层精密算法构成：

```text
[HTTP 请求: GET /threads/{id}/stream 或 POST /runs/stream]
  │ (带 Last-Event-ID: <seq>-0 头)
  ▼
[_thread_cursor] ──> 解析校验游标格式 (必须为数字或合法 Redis Stream ID)
  │
  ▼
[_thread_events] ──> 从 PostgreSQL runtime_events 表拉取 sequence > cursor 的行
  │                   (若 cursor < watermark 则返回 cursor_expired 引导客户端拉全量快照)
  ▼
[SSE 格式化输出] ──> 逐行生成 id: <seq>-0 \n event: ... \n data: ... \n\n
  │
  ▼
[Redis 实时队列] ──> 消费 StreamManager 实时推入的 Message 帧
  │
  ▼
[: heartbeat\n\n] ──> 超过 15 秒空闲自适应发送心跳注释行
```

### 2. 真实工程实战：反向代理零缓冲与心跳注入
在项目 [20260927-sse-stream-heartbeat-and-resilience](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/docs/projects/20260927-sse-stream-heartbeat-and-resilience/README.md) 中，GraphHarbor 沉淀了关键生产防护参数：

查阅 `streaming.py` 中的响应头构造：
```python
return StreamingResponse(
    body(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache, no-transform", # 禁止任何网关缓存或压缩转换
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",                 # 强制指示 Nginx 立即下发字节，严禁缓冲拼包！
    },
)
```
同时，心跳间隔硬编码自适应算法：
```python
heartbeat = max(float(os.environ.get("GRAPHHARBOR_THREAD_STREAM_HEARTBEAT_SECONDS", "15")), 0.1)
# ...
if not emitted and (loop.time() - last_sent_at >= heartbeat):
    yield ": heartbeat\n\n"
    last_sent_at = loop.time()
```
以标准的冒号开头（`: heartbeat\n\n`）作为 SSE 协议规定的合法注释行下发，所有符合 W3C 标准的客户端解析器会自动静默忽略它，但传输层 TCP 连接因此保持高度活跃！

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：如果客户端断线太久，请求的 `Last-Event-ID` 对应的历史事件已经被后台定时任务清理了（游标落后于 Watermark），怎么办？
> **老王冷哼一声**：“这就叫失效降级！你总不能把半年前的垃圾事件一直留着占满磁盘吧？！”
> 
> 查阅 `streaming.py` 第 251~256 行：
> ```python
> if (request.headers.get("last-event-id") not in (None, "-") and cursor_value < watermark):
>     yield _sse("error", {"detail": "cursor_expired", "recovery": "thread_snapshot"})
>     return
> ```
> 系统绝不会强行返回不连续的残缺事件欺骗前端。一旦检测到游标已过期，立即下发结构化错误帧，指示前端通过调用 `GET /threads/{id}/state` 获取完整的最新状态快照（Thread Snapshot）来重置界面，既保证了数据绝对一致，又给后台事件清理留出了充足空间！

### 2. 工业级避坑清单
- ⚠️ **避坑 1：必须在 `finally` 块彻底注销监听队列**
  在 `thread_stream` 中，必须使用 `try...finally: await manager.remove_thread_stream(thread_id, queue)`。如果客户端中途强关浏览器，协程被 Cancelled，漏掉注销会导致 Redis 内存订阅字典无限泄露！
- ⚠️ **避坑 2：SSE 事件 ID 必须严格追加在末尾或首部并保序**
  官方 SDK 依赖 `id: <seq>-0` 来追踪执行进度。查阅 `_sse` 函数，GraphHarbor 支持 `event_id_last=True`，将 `id` 行紧贴在末尾双换行前，完全符合官方协议对事件完整性的解析习惯。
- ⚠️ **避坑 3：长连接内部定期重新校验授权（Auth Re-verification）**
  智能体推理可能持续数分钟。系统在 `streaming.py` 中设计了每隔 10 秒调用一次 `_check_authorized()`。如果在执行过程中该 Thread 被管理员从后台物理删除或撤销了权限，长连接会在 10 秒内主动掐断，防止敏感数据持续泄密！

---

## 四、架构不变量清单（Architectural Invariants）

1. **游标连续回放不变量（Cursor Continuous Replay Invariant）**：
   在客户端传递合法 `Last-Event-ID` 且未过期的前提下，系统必须先精准补发所有未传的历史事件帧，才允许放行实时流，严禁跳帧或漏发。
2. **保活注释行格式不变量（Heartbeat Comment Invariant）**：
   空闲期注入的保活心跳必须严格使用原生 SSE 注释行（`: heartbeat\n\n`），绝不允许包装成业务事件帧（如 `event: ping`），防止污染前端图状态。
3. **长连接安全审计不变量（Long-Lived Stream Audit Invariant）**：
   长连接活跃期内必须周期性核验资源访问权限，任何底层资源状态变更必须在 10 秒内同步反映到长连接的存活状态上。
