# 02-租约生命周期：心跳续约、租约过期判定与自愈收割机极限推演

> **概念定位与核心价值**：本专篇深度解密 GraphHarbor 高可用架构中保障长耗时智能体任务永不丢失的**“死人开关（Dead Man's Switch）”机制**。详尽阐释基于 PostgreSQL 权威持久化的**分布式租约（Run Lease）、双层心跳续约与自愈收割机（The Reaper）**的端到端演进逻辑，彻底解决分布式微服务因节点崩溃、宿主机断电引发的“永久 Running 状态死锁”。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
水力发电站的特级泄洪大坝控制室：
- 泄洪闸门由主操作工按压控制（Worker 节点正在执行推理）；
- 控制台上装有一个**“失能安全手柄（死人开关）”**：操作工必须每隔 5 分钟向监控总机打卡一次，证明自己意识清醒、生命体征正常（心跳续签）；
- 如果操作工突发心梗晕倒（容器被 OOM Kill）或者控制室线缆着火（机房网络断连），超过 10 分钟没有打卡；
- 监视总控台上的**备用自愈机械臂（The Reaper 收割机）**立刻被触发，强制切断旧操作台的控制回路，把报警代际推高一级，并将闸门控制权无缝转交给备用控制室的健康工人（重新入队抢占）！

### 2. 软件工程演进痛点
在传统的 Agent 任务调度设计中，“进程死亡”是运维最大的噩梦：
1. **任务僵死永久挂起**：Worker 在执行一个长达 3 分钟的复杂多智能体代码生成任务时，宿主机物理机发生硬件故障直接断电。数据库里的 `runs` 表永久停留在 `status='running'`，前端界面一直在转圈等待，客户端以为还在计算，用户等待了几个小时直到超时崩溃；
2. **人肉运维进库改 SQL 的耻辱**：早期玩具系统发生故障后，群里疯狂报警，运维人员慌慌张张连上生产数据库，执行 `UPDATE runs SET status='error' WHERE id=...`。这不仅容易改错条件引发大面积事故，还会导致事件日志与状态机版本完全脱节；
3. **假死与真死无法识别**：普通的超时机制只看整体运行时间（如限时 10 分钟）。如果一个任务正常需要运行 15 分钟，粗暴超时会直接误杀正在辛勤计算的健康任务；而租约心跳关注的是“**当前计算节点是否还活着**”，只要心跳按时打卡，超长任务就能合法无限续期！

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：无租约保护，节点一旦暴毙，任务永久死锁
async def naive_execute_run(run_id):
    # 致命伤：标记为 running 后没有任何心跳续期机制
    # 只要该进程遭遇 kill -9，数据库记录就成了永久的“植物人”状态
    await db.execute("UPDATE runs SET status='running' WHERE id=%s", run_id)
    await run_llm_chain()  # 若此处进程被操作系统强杀，永远无法走到下一行
    await db.execute("UPDATE runs SET status='success' WHERE id=%s", run_id)

# ✅ 生产级落地方案 (GraphHarbor Production)：行级租约 + 伴生心跳 + 独立自愈收割机
async def production_execute_run(worker, run_id):
    async with connect() as conn:
        # 核心防守 1：抢占时原子设置租约绝对过期时间 (expires_at = now() + 60s)
        await repository.claim_pending(conn.session, run_id, worker.owner, lease_seconds=60)
    
    # 核心防守 2：启动伴生心跳协程，每隔 5 秒刷新 Redis 探针与 PG 租约
    heartbeat_task = asyncio.create_task(worker.heartbeat_loop(run_id))
    try:
        await worker.execute_steps(run_id)
    finally:
        heartbeat_task.cancel()  # 正常结束或异常退出均释放心跳
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 源码精准定位与执行拓扑
收割机与租约自愈系统在工程中分为前台伴生与后台守护两条主线：

```text
[前台主线: 任务执行与心跳打卡]
ProductionWorker.run_once()
  │ (1) claim_pending() 写入 run_leases(expires_at = now() + 60s, generation = 1)
  ▼
background_heartbeat()
  │ (2) 每 5 秒调用 set_run_heartbeat() (Redis) 与 UPDATE run_leases.heartbeat_at (PG)
  ▼
execute_graph_steps() (大模型推理运算)

────────────────────────────────────────────────────────────────────────────

[后台主线: Reaper 独立巡检与无主自愈]
ProductionWorker._reaper_loop() (每 5 秒周期性触发)
  │
  ▼
reap_once() ──> complete_rollbacks() (优先清理前序脏镜像)
  │
  ▼
RunRepository.requeue_expired()
  │ (3) 原子 SELECT ... FOR UPDATE 扫描 expires_at < now() 的僵尸任务
  │ (4) 将 runs.retry_count (Generation) 递增 (+1)
  │ (5) 重置 runs.status = 'pending'，删除过期 run_leases 行
  ▼
wake_run_queue() ──> 广播唤醒全集群存活 Worker 重新抢占！
```

### 2. 原子收割与代际跃迁的 SQL 级防线
查阅 [`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/ops.py)，收割逻辑通过数据库行级排他锁保证原子性：

```python
async def requeue_expired(self, session: AsyncSession) -> int:
    now = datetime.now(UTC)
    # 1. 查找租约绝对超时的无主任务
    query = (
        select(RunLeaseRow.run_id, RunLeaseRow.generation)
        .where(RunLeaseRow.expires_at < now)
        .with_for_update(skip_locked=True)  # 并发收割时跳过已锁定的行，零冲突
    )
    expired_leases = (await session.execute(query)).all()
    if not expired_leases:
        return 0

    for run_id, current_gen in expired_leases:
        # 2. 单调递增代际，生成新的 Fencing Token
        next_gen = current_gen + 1
        # 3. 将任务还原为 pending，清除已死 Worker 的归属，等待新节点接管
        await session.execute(
            update(RunRow)
            .where(RunRow.run_id == run_id, RunRow.status == "running")
            .values(
                status="pending",
                lease_owner=None,
                lease_expires_at=None,
                retry_count=next_gen
            )
        )
        await session.execute(delete(RunLeaseRow).where(RunLeaseRow.run_id == run_id))
    return len(expired_leases)
```

### 3. 伴生事件修剪（Event Pruning Retention）
在 `_reaper_loop` 中，收割机在完成租约巡检后，还会顺带执行历史事件清理：
- 根据 `GRAPHHARBOR_EVENT_RETENTION_SECONDS`（默认保留 86400 秒即 24 小时）；
- 每次按 `GRAPHHARBOR_EVENT_PRUNE_BATCH_SIZE`（默认 1000 条）执行分批软删除与修剪；
- 极大缓解了高频打字机实时事件导致 PostgreSQL `runtime_events` 表无限膨胀的存储压力。

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：多节点部署多个 Worker，每个节点都在跑 Reaper，会不会打架打崩数据库？
> **老王怒喷**：“你以为老王我写代码不加并发锁吗？！”
> 
> 1. **单进程内 Slot 隔离**：查阅 `production_worker.py` 的 `run_worker()`，单进程内如果有多个并发槽位（Slot），**系统硬编码只有 `slot == 0` 的 Worker 实例才会启动 `_reaper_loop`**，杜绝多槽位自相惊扰；
> 2. **跨进程数据库行级无锁跳过（`skip_locked=True`）**：在 SQL 层使用了 PostgreSQL 独有的 `FOR UPDATE SKIP LOCKED`。当多个节点的 Reaper 同时扫描表时，已被其他节点选中的超时行会被自动跳过，彼此井水不犯河水，并发安全且性能极高！

### 2. 工业级避坑清单
- ⚠️ **避坑 1：租约时长与心跳间隔绝对严禁倒挂**
  租约时长（`lease_seconds`）必须至少为心跳刷新间隔（`heartbeat_refresh_interval`）的 **3 到 5 倍**（例如心跳 5 秒刷新一次，租约严禁设在 15 秒以下）。否则一旦发生网络毫秒级毛刺，健康任务会被收割机误判死亡直接抢走！
- ⚠️ **避坑 2：优雅下线必须调用 `requeue_for_shutdown`**
  当容器收到 `SIGTERM` 准备停止时，必须主动撤销自己的租约，将任务重置为 `pending`。绝不能让任务苦等 60 秒租约超时才被收割，这会严重降低发布期间的吞吐流转速度！
- ⚠️ **避坑 3：心跳更新严禁与业务主事务共用连接**
  心跳协程必须使用独立的短平快连接去写数据库或 Redis。如果心跳跟图节点的复杂查询混在同一个大事务里，主事务执行慢就会拖垮心跳刷新，最终自己把自己“误杀”！

---

## 四、架构不变量清单（Architectural Invariants）

1. **死人开关绝对生效不变量（Dead Man's Switch Invariant）**：
   任何处于 `running` 状态的任务，在数据库中必须同时具备有效且未过期的 `run_leases` 记录。一旦租约超时，该任务在逻辑上被视作已死亡，必须无条件被收割重入队。
2. **收割代际单向递增不变量（Monotonic Requeue Invariant）**：
   收割机每次执行 `requeue_expired`，必须且只能将该任务的 `retry_count`（Generation）加 1，绝不允许在同一代际内进行二次抢占。
3. **优雅停机零饥饿不变量（Graceful Shutdown Requeue Invariant）**：
   Worker 捕获停机信号后，正在处理的任务必须无损退回 `pending` 状态，且不得递增其错误重试计数，确保新节点能够即刻零代价拾取。
