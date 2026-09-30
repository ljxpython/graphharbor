# 01-生产防裂脑：Fencing Token、Generation 代际递增与过期写拦截

> **概念定位与核心价值**：本专篇深度解密分布式智能体运行时中最隐蔽、最致命的**“僵尸写穿（Zombie Stale Write）与脑裂”**风险。详尽剖析 GraphHarbor 如何基于 Martin Kleppmann 的经典分布式理论，在 PostgreSQL 存储层落地 **Fencing Token 与 Generation 单调递增校验机制**，确保即使在发生机房断网、Worker 节点长 GC 假死等极端事故时，智能体的历史状态树也绝不被陈旧快照污染。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
五金店老王有一台昂贵的工业级大功率柴油发电机，出租给工地使用，配了一块**带动态代际芯片的智能钥匙牌**：
- 老王把发电机租给张三（代际标号 `Generation = 1`），约定租期 2 小时；
- 2 小时过去，张三的对讲机彻底失联（网络分区）。老王判定张三违约弃租，立刻远程将发电机的防盗锁心升级（`Generation = 2`），并把新钥匙交给了李四；
- 李四开机施工，发电机平稳运转；
- 此时，张三突然满头大汗从坑道里爬出来，以为还是自己的租期，抄起手里的旧钥匙（`Generation = 1`）试图插入点火孔！

如果发电机是个只看物理齿轮的“傻瓜锁”，张三强行拧动钥匙，两台点火电机同时暴力啮合，瞬间打烂变速箱甚至引发大爆炸！
但因为老王加了**代际芯片锁**，锁孔感应到张三的钥匙代际是 `1`，而内部锁心已经更新为 `2`，锁芯防盗机构当场“咔哒”一声强行锁死，并拉响警报（抛出 `CheckpointConflict`）！

### 2. 软件工程演进痛点
在分布式多 Worker 架构中，所有团队都会遇到以下幽灵般的生产故障：
1. **网络瞬断与 GC 假死**：Worker 1 在执行一个大模型长耗时推理时，因容器宿主机资源争抢发生 20 秒的长 Full GC 停顿或遭遇网络抖动；
2. **租约失效与任务重分发**：调度中心（Reaper）发现 Worker 1 超过租约期限未发心跳，判定其已暴毙，于是将该任务的重试计数递增（`retry_count: 1 -> 2`），并由空闲的 Worker 2 抢占租约继续执行；
3. **僵尸写穿（Zombie Stale Write）**：Worker 1 突然从假死中缓过神来，它根本不知道自己已经被“剥夺政治权利”，继续欢快地把 20 秒前计算出的 Step 1 Checkpoint 写入数据库；
4. **状态时光倒流灾难**：此时 Worker 2 可能已经在数据库写完了 Step 2 和 Step 3。Worker 1 的陈旧写入瞬间将数据库中的最新状态暴力覆盖回滚！整个智能体多轮对话历史彻底错乱，用户收到驴唇不对马嘴的胡言乱语。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：无保护直接盲写，任何持有数据库连接的 Worker 均可修改
async def naive_put_checkpoint(thread_id, checkpoint_data):
    # 致命伤：只管 INSERT，压根不校验写者当下的真实合法性
    # 只要旧 Worker 连接尚存，哪怕它的租约 10 分钟前就失效了，依然能成功写入并覆盖新数据
    await db.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (%s, %s, %s)",
        (thread_id, checkpoint_data["id"], checkpoint_data)
    )

# ✅ 生产级落地方案 (GraphHarbor Production)：行级锁排他 + Fencing Token 世代单调递增核验
class FencedPostgresSaver(AsyncPostgresSaver):
    @asynccontextmanager
    async def _writer(self, config: RunnableConfig):
        writer = checkpoint_writer.get()  # 获取当前协程上下文凭证 (run_id, owner, generation)
        async with self.conn.connection() as conn, conn.transaction():
            if writer:
                run_id, owner, generation = writer
                # 核心防守：行级锁排他查出数据库中的真实权威状态
                cursor = await conn.execute(
                    "SELECT status, lease_owner, retry_count, lease_expires_at > now() AS is_valid "
                    "FROM runs WHERE run_id=%s FOR UPDATE", (run_id,)
                )
                run = await cursor.fetchone()
                # 任何一条不匹配：状态非 running、租约已超时、所有者变动、代际落后，当场击毙！
                if (not run or run["status"] != "running" or not run["is_valid"]
                    or run["lease_owner"] != owner or run["retry_count"] != generation):
                    raise CheckpointConflict("checkpoint writer no longer owns the run")
            yield AsyncPostgresSaver(conn, serde=self.serde)
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 源码精准定位与调用链路
防脑裂机制在 GraphHarbor 内部通过三层纵深防御构成：

```text
[production_worker.py]
  │ (1) 抢占任务成功后，将 (run_id, worker_id, run.retry_count) 注入 ContextVar
  ▼
[checkpoint_mutations.py: checkpoint_writer]
  │ (2) 伴随协程调用栈隐式传递，避免上层 Graph 业务代码感知
  ▼
[checkpoint.py: FencedPostgresSaver._writer]
  │ (3) 在真正执行 SQL 插入前，开辟事务并获取行级排他锁 (FOR UPDATE)
  ▼
[PostgreSQL: runs & checkpoints 表]
    (4) 验证 runs.retry_count == generation，不符则立即回滚并抛出 CheckpointConflict
```

### 2. 真实工程实战：放行活跃 Run 自身 Checkpoint 写入
在实际工程演进中（2026-09-30 修复），GraphHarbor 攻克了一个极其隐蔽的陷阱：某些内部图节点或官方调度器在执行中间步骤时，由于处于原生异步子任务中，可能未显式继承外部 `checkpoint_writer` 凭证。

查阅 [`checkpoint.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/checkpoint.py) 源码（第 67~96 行），系统设计了极为精密的兜底防御：

```python
if not writer:
    # 从 config 的 configurable 或 metadata 中多层嗅探 run_id
    cfg_dict = config if isinstance(config, dict) else {}
    configurable = cfg_dict.get("configurable") or {}
    metadata = cfg_dict.get("metadata") or {}
    req_run_id = configurable.get("run_id") or metadata.get("run_id") or cfg_dict.get("run_id")
    
    # 查询当前线程下是否处于活跃运行或回滚中
    cursor = await connection.execute(
        "SELECT run_id, status, reason FROM runs WHERE thread_id=%s "
        "AND (status='running' OR reason='rollback')",
        (resource_id,),
    )
    active_runs = await cursor.fetchall()
    for r in active_runs:
        active_run_id = r["run_id"]
        # 如果存在正在运行的任务，且不是当前 Run 自身，判定为非法并发写冲突！
        if current_run_uuid is None or active_run_id != current_run_uuid:
            raise CheckpointConflict("thread has an active run or rollback")
```
这种设计既放行了合法活跃任务自身的中间步骤落盘，又死死封死了任何第三方并行的脏写入！

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：为什么不用 Redis 分布式锁来做防裂脑，而要跑回数据库查表？
> **老王怒骂**：“拿 Redis 锁来防脑裂的，都是没挨过分布式系统毒打的半吊子！”
> 
> 分布式系统泰斗 Martin Kleppmann 早就证明过著名的 **Redlock 缺陷**：
> - Redis 分布式锁必然带有 TTL（超时时间）。
> - 假设 Worker 拿到 Redis 锁，准备写数据库；就在这时，Worker 遭遇了 10 秒的 GC 假死。
> - 此时 Redis 里的锁**因为超时自动释放了**！另一个新 Worker 拿到锁，并成功向数据库写入了新数据；
> - 假死的 Worker 缓过劲来，它以为自己还持有锁，顺手就往数据库写入脏数据——**Redis 锁在存储层面前根本防不住任何物理穿透！**
> 
> **唯一的真理**：**防守必须做在状态修改的最底层（即存储介质事务边界内）！**
> GraphHarbor 采用的方案是将 Token（`retry_count` 世代）作为版本栅栏，写入 Checkpoint 时在 PostgreSQL 事务内执行 `SELECT ... FOR UPDATE` 强一致性校验。就算 Redis 瘫痪了、丢锁了，PostgreSQL 这道铁门也绝不允许旧代际写入半个字节！

### 2. 工业级避坑清单
- ⚠️ **避坑 1：严禁在异步协程切换中丢失 ContextVar**
  `checkpoint_writer` 依赖 Python 的 `contextvars`。如果二次开发人员使用了某些遗留线程池或非标准的裸 `Thread` 执行代码，会导致 ContextVar 丢失。如果需要跨线程，必须显式使用 `contextvars.copy_context().run(...)`！
- ⚠️ **避坑 2：严禁直接在数据库手动 `UPDATE runs SET retry_count = xxx`**
  人工私自调整 `retry_count` 会破坏世代单调递增性，导致正常正在运行的 Worker 在下一个 Step 保存 Checkpoint 时突发 `CheckpointConflict` 崩溃。
- ⚠️ **避坑 3：严禁在 `_writer` 内部进行网络 RPC 长耗时调用**
  `FencedPostgresSaver._writer` 持有了数据库连接与行级锁。必须保证该代码块内只有纯粹的数据库读写操作，严禁在事务持有期间调用第三方大模型接口，否则连接池瞬间耗尽！

---

## 四、架构不变量清单（Architectural Invariants）

1. **底层存储最终裁决不变量（Storage-Level Fencing Invariant）**：
   防脑裂校验必须在 PostgreSQL 的 ACID 事务内部完成，绝不依赖上游调用方或内存缓存的自觉性。
2. **世代单调递增不变量（Monotonic Generation Invariant）**：
   `runs.retry_count` 代表当前任务的权威世代（Generation），任何对同一任务的重新抢占或重试必须单调递增此代际号。
3. **互斥写事务回滚不变量（Atomic Conflict Rollback Invariant）**：
   一旦触发 `CheckpointConflict`，底层事务必须无条件回滚，严禁捕获该异常后静默忽略或强行写入！
