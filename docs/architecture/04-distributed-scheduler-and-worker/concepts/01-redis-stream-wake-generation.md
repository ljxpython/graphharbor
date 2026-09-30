# 01-高并发防假死：Wake Generation 单调递增与 Redis Pub/Sub 惊群抑制

> **概念定位与核心价值**：本专篇深度解密高并发异步事件驱动编程中最经典的**“丢唤醒（Lost-Wakeup Race）”与“惊群震荡（Thundering Herd）”**难题。详尽阐释 GraphHarbor 如何在 Redis Pub/Sub 广播与本地 asyncio 调度循环之间，精妙引入**单调递增代际计数器（`_WAKE_GEN`）**，彻底根除高并发吞吐场景下 Worker 节点离奇陷入挂起假死（Hanging）的顽疾。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
老王带徒弟小李去某家网红理发店排队理发：
- 理发店大堂有一个大喇叭（Redis Pub/Sub 广播），只要有空闲理发椅，广播就会大喊一声：“来个顾客！”；
- 小李正坐在等候区（Worker 空闲等待态）。就在广播大喇叭响起的同时，小李正好低头系鞋带（执行了 `event.clear()` 的瞬间）；
- 小李系好鞋带抬起头，心想：“刚才没听到喇叭响啊”，于是放心地靠在椅背上闭目养神（进入 `await event.wait()`）；
- 结果：明明有空闲椅子，小李却在沙发上干坐了整整一个下午，直到店里打烊关门都没排上队——**这就是经典的丢唤醒挂死！**

怎么彻底根除这个问题？**墙上挂一个不断递增的机械翻牌器（Wake Generation）**：
每次前台有新号，翻牌器数字就 `+1`。小李每次闭眼之前，先看一眼墙上的数字是 `10`。如果系完鞋带再看一眼数字变成了 `11`，说明刚才系鞋带的瞬间有人叫过号了！小李立刻起身冲向柜台，绝不睡觉！

### 2. 软件工程演进痛点
在 Python 的 `asyncio` 高并发事件循环中，许多工程师习惯性地使用 `asyncio.Event` 来做跨任务通知。然而，在密集任务突发的场景下，这会引发致命的竞态条件：
1. **Wait/Clear 窗口期丢唤醒**：Worker 在处理完上一批任务后，执行 `event.clear()` 重置标志位，紧接着准备调用 `await event.wait()`。如果在这两个操作的微秒级缝隙间，Redis 监听到新任务并执行了 `event.set()`，紧随其后的 `event.clear()` 会将这个刚刚到来的通知无情抹杀！Worker 随即进入无限等待，直到下一次超时唤醒（可能长达数秒甚至永久挂起）；
2. **多副本惊群爆炸**：当有 50 个 Worker 副本同时监听 Redis 队列时，若采用粗暴的广播机制，每次来一个任务，所有 50 个 Worker 同时被惊醒并向 Redis 发起争抢，导致网络带宽与连接池瞬间打满，而最终只有 1 个 Worker 抢到任务，其余 49 个全部白白浪费 CPU。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：直接裸用 asyncio.Event，高并发下频繁发生信号丢失
class NaiveWakeManager:
    def __init__(self):
        self.wake_event = asyncio.Event()

    async def wait_for_job(self):
        # 致命伤：如果在处理业务与重置标志位之间，新任务的 set() 恰好到达，
        # clear() 会直接把信号吃掉，导致协程死锁在 wait() 上无法自愈！
        self.wake_event.clear()
        await self.wake_event.wait()

# ✅ 生产级落地方案 (GraphHarbor Production)：单调递增代际计数器 (Wake Generation)
class ProductionWakeManager:
    def __init__(self):
        self._wake_event = asyncio.Event()
        self._wake_gen = 0  # 单调递增的代际数

    def signal_wake(self):
        self._wake_gen += 1
        self._wake_event.set()

    async def wait_for_queue_wake(self, timeout=0.5):
        start = self._wake_gen
        if self._wake_event.is_set():
            self._wake_event.clear()
            # 核心防守：如果清空事件的瞬间代际又被推高了，立刻重新补设 set()！
            if start != self._wake_gen:
                self._wake_event.set()
            return True
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        except TimeoutError:
            return start != self._wake_gen  # 超时前若代际变动，依然视为已被唤醒
        end = self._wake_gen
        self._wake_event.clear()
        if end != self._wake_gen:
            self._wake_event.set()
        return True
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 源码精准定位与调用链路
查阅 [`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/redis_stream.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/redis_stream.py) 源码（第 980~1056 行），系统构建了极为精密的唤醒防护网：

```text
[网关 enqueue_run] ──> PUBLISH graphharbor:runtime:run-queue "wake"
                                │
                                ▼ (跨网络到达 Worker 进程)
                   [_wake_listener_loop (后台长监听)]
                                │
                                ▼
                       [_signal_local_wake]
                                │ (1. 全局 _WAKE_GEN += 1)
                                │ (2. _WAKE_EVENT.set())
                                ▼
[ProductionWorker.run_forever] ──> [wait_for_queue_wake]
                                      (代际比对：杜绝 wait/clear 竞态信号丢失)
```

### 2. 双重代际守卫的高保真解析
深入分析 `wait_for_queue_wake` 的两处核心防守：

```python
async def wait_for_queue_wake(timeout: float = 0.5) -> bool:
    if _WAKE_EVENT is None:
        await asyncio.sleep(timeout)
        return False
        
    start = _WAKE_GEN
    # 防守 1：如果进入时事件已经是 set 状态
    if _WAKE_EVENT.is_set():
        _WAKE_EVENT.clear()
        # 如果在 clear() 的一刹那，另一个协程刚好执行了 _signal_local_wake() 并推高了 _WAKE_GEN
        # 必须主动重新恢复 set() 状态，防止下一个并发消费者丢失通知！
        if start != _WAKE_GEN:
            _WAKE_EVENT.set()
        return True

    try:
        await asyncio.wait_for(_WAKE_EVENT.wait(), timeout=timeout)
    except TimeoutError:
        # 防守 2：即便触发了超时，只要代际被推高了，立刻判定为唤醒成功
        return start != _WAKE_GEN
    except asyncio.CancelledError:
        raise
        
    end = _WAKE_GEN
    _WAKE_EVENT.clear()
    if end != _WAKE_GEN:
        _WAKE_EVENT.set()
    return True
```

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：既然 Redis Streams 原生支持 `XREAD(BLOCK=...)`，为什么还要额外搞一套 Pub/Sub 广播？
> **老王反唇相讥**：“让几十个 Worker 一天 24 小时死挂在 Redis Streams 上长轮询？你的连接池不用要了？！”
> 
> 1. **连接与空转成本**：`XREAD BLOCK` 会持续占用 Redis 的连接与 Socket 缓冲区。在无任务积压的夜间或平峰期，成百上千个空转连接不仅挤占 Redis 资源，在遭遇网络抖动时还会引发大规模重连风暴；
> 2. **轻重分离解耦**：GraphHarbor 将**调度通知（Lightweight Notification）**与**数据拉取（Heavyweight Fetch）**彻底解耦：
>    - 通知走微秒级的 Pub/Sub 广播（`PUBLISH run-queue wake`），无持久化负担，消费极快；
>    - Worker 被唤醒后，再去队列中 `LPOP` 弹出任务 Hint。如果没抢到，立刻借助代际计数器安静休眠，绝不制造无效轮询。

### 2. 工业级避坑清单
- ⚠️ **避坑 1：Redis 连接池大小严禁使用库默认值**
  `redis-py` 默认连接池容量偏小（通常为单连接或极低上限）。在高并发场景下，查阅 `redis_stream.py` 中的 `_redis_max_connections()`，系统强制将最小连接数提至 64 以上，二次开发严禁将其配小，否则高负载下直接报 `ConnectionPool exhausted`！
- ⚠️ **避坑 2：Pub/Sub 监听任务崩溃自愈**
  `_wake_listener_loop` 是后台长期存活的单一守护任务。如果 Redis 临时抖动，该任务必须在 `catch Exception` 后执行 `_reconnect_backoff()` 指数退避重连，绝不允许发生未捕获异常导致监听协程彻底消亡！
- ⚠️ **避坑 3：严禁在 `_signal_local_wake` 中执行阻塞调用**
  该函数在 Pub/Sub 消息到达的纳秒级时间内触发，内部只做简单的整数递增和事件标记，严禁在其中塞入任何日志打点或网络 I/O，否则将直接拖慢整个 Redis 传输管道。

---

## 四、架构不变量清单（Architectural Invariants）

1. **代际单调递增不变量（Monotonic Wake Generation Invariant）**：
   全局 `_WAKE_GEN` 只能单向递增，绝不允许在任何重启之外的场景中重置为零，确保唤醒比对具有全局因果序。
2. **通知丢弃自愈不变量（Self-Healing Clear Invariant）**：
   在任何清空 `_WAKE_EVENT` 的逻辑后，必须立刻比对前后代际计数器；只要检测到并发出新信号，必须无条件还原 `set()` 状态。
3. **自适应超时兜底不变量（Bounded Sleep Fallback Invariant）**：
   Worker 监听等待的最大超时时间严禁超过 0.5 秒（`timeout <= 0.5`），即便 Pub/Sub 网络完全断裂，Worker 也必须能凭借轮询兜底从数据库捡拾任务。
