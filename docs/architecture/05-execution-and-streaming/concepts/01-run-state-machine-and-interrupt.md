# 01-复杂状态机：Human-in-the-loop（中断/审批/回滚/恢复）跃迁全景图

> **概念定位与核心价值**：本专篇深度解密 GraphHarbor 如何将 LangGraph 的图断点拦截机制与生产级**确定性有限状态机（Deterministic Finite State Machine, DFSM）**相结合，构建出高并发、零资源浪费的 **Human-in-the-loop（人类在环审批与人机协同）状态机闭环**。详述智能体在触发中断时如何优雅将物理计算资源“归零”，以及如何在获得人类授权后精准复现现场继续执行。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
老王去银行柜台办理一笔 500 万元的大额转账业务：
- 柜员在电脑上录入完账号和金额，刚点下“执行”，系统屏幕弹出一个醒目的红色对话框：“**转账金额超过 100 万元，必须由支行行长插入物理授权 UKey 并进行人脸识别（HITL 中断）**”；
- 此时柜员的电脑绝对不会傻傻卡死在那里搞死循环等待，而是把这笔单据打上“待行长审批”的挂起标记，保存进数据库，柜员转头就叫号给下一位办业务的大爷存退休金；
- 行长下午开完会过来，在系统里调出这笔挂起的单据，插上 UKey 确认通过（`Command.resume`）；
- 系统瞬间调出当时的转账表单现场，毫秒级完成扣款和汇出。

如果银行系统写得很弱智：行长不来，柜员的电脑就一直卡死在“等待行长”的旋转界面，谁也不能用这台电脑，银行大厅瞬间排起几百人的长队，甚至中途电脑跳闸断电，这笔业务就成了找不到去向的坏账！

### 2. 软件工程演进痛点
在让 AI 智能体自主调用复杂工具（如退款、修改生产数据、发送群发邮件）时，必须设置人工审批防线。很多架构设计不成熟的系统往往踩中严重设计缺陷：
1. **进程阻塞等待反模式（Blocking Poll Anti-Pattern）**：在代码里写 `while not approved: sleep(1)`。一个需要等待人类领导三天后才能批准的请假单，会直接把昂贵的 GPU/CPU 计算 Worker 和数据库长连接霸占整整三天！几百个审批任务瞬间将整个微服务集群的线程池彻底拖死；
2. **状态跃迁非确定性（State Drift & Ambiguity）**：没有严格的状态转移矩阵，任何接口都可以随意把任务标为任意状态。当一个已经被取消（`interrupted`）的任务突然被网络重试请求误设为 `running`，系统将发生严重的逻辑错乱与二次执行；
3. **现场丢失无法复原**：中断发生时只记了一个标志位，没有把图节点执行到一半的私有通道变量、父子调用关系精准快照。人类批准后，智能体只能从第一步重新再推理一遍，浪费昂贵的 Token 成本。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：在 Worker 进程内原地阻塞死等，资源霸占崩溃
async def naive_hitl_node(state):
    if state["requires_approval"]:
        # 致命伤：原地死循环等待，Worker 进程被物理锁死，机器瞬间资源枯竭
        while not await check_human_approval(state["task_id"]):
            await asyncio.sleep(5)
    return {"status": "executed"}

# ✅ 生产级落地方案 (GraphHarbor Production)：状态机落盘挂起 + 彻底释放 Worker 资源
async def production_hitl_flow(worker, run_id, result):
    if getattr(result, "interrupts", None):
        async with connect() as conn:
            # 核心防守 1：DFSM 将 Run 状态推进至 INTERRUPTED，同步更新 Thread.interrupts
            await repository.finish(
                conn.session, run_id, worker.owner,
                status=RunStatus.INTERRUPTED, reason=RunReason.HITL_INTERRUPT,
                terminal_payload={"interrupts": result.interrupts}
            )
        # 核心防守 2：彻底退出当前 Worker 循环并释放租约，等待人类通过 API 发送 Resume Command
        return True
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 确定性状态机转移矩阵 (`run_state.py`)
查阅 [`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_state.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/run_state.py)，系统以白名单形式固化了状态跃迁规则：

```python
_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset(
        {RunStatus.PENDING, RunStatus.RUNNING, RunStatus.ERROR, RunStatus.INTERRUPTED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.PENDING,      # 租约超时或优雅停机重入队
            RunStatus.RUNNING,
            RunStatus.SUCCESS,      # 正常执行完成
            RunStatus.ERROR,        # 业务失败或超重试上限
            RunStatus.TIMEOUT,      # 运行硬超时
            RunStatus.INTERRUPTED,  # 人工审批中断或客户端显式取消
        }
    ),
    RunStatus.INTERRUPTED: frozenset({RunStatus.INTERRUPTED, RunStatus.PENDING}), # 唯一合法出路：人类恢复重新入队
    RunStatus.SUCCESS: frozenset({RunStatus.SUCCESS}), # 终态不可逆
    RunStatus.ERROR: frozenset({RunStatus.ERROR}),     # 终态不可逆
    RunStatus.TIMEOUT: frozenset({RunStatus.TIMEOUT}), # 终态不可逆
}
```
任何未在白名单中列出的跨状态跳转（如从 `SUCCESS` 试图跳回 `RUNNING`），直接抛出 `InvalidTransition` 并在数据库事务层面当场回滚阻断！

### 2. 审批恢复指令解析 (`resume_command`)
当人类在前端点击“确认授权”并提交参数时，查阅 [`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/graph_executor.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/graph_executor.py)，系统负责将外部 JSON 反序列化为 LangGraph 内部的精准指令：

```python
def resume_command(value: Any) -> Command | None:
    """Convert the public run command envelope to LangGraph's resume input."""
    if not isinstance(value, dict):
        return None
    # 提取合法恢复指令字段：graph, update, resume, goto
    fields = {key: value[key] for key in ("graph", "update", "resume", "goto") if key in value}
    return Command(**fields) if fields else None
```
该命令注入执行后，LangGraph 会精准将恢复值（`Command.resume`）灌入挂起的 `interrupt()` 表达式并继续向后推导图节点，无需重新执行之前的任何节点。

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：如果用户驳回审批（Cancel）并要求修改历史状态重跑，怎么防状态撕裂？
> **老王怒拍桌子**：“状态机最忌讳半吊子的人工‘状态覆写’！你敢裸改字段，整个 Checkpoint 链条就断了！”
> 
> GraphHarbor 的工程解法：
> 1. **不可篡改的历史版本链**：每个 Checkpoint 都持有 `parent_checkpoint_id`。如果人类在审批界面驳回并修改了输入，系统不是去原地覆写旧快照，而是在指定父节点上**分叉出一个新的分支快照**；
> 2. **回滚基线对齐（Rollback Alignment）**：如果用户直接取消，系统通过单事务还原 `run_checkpoint_baselines`，将 Thread 还原至该 Run 启动前的绝对纯净态，杜绝残留半吊子中间结果。

### 2. 工业级避坑清单
- ⚠️ **避坑 1：严禁私自将终态任务逆向改回活跃态**
  `SUCCESS`, `ERROR`, `TIMEOUT` 是物理终态。如果业务需要重试已失败的任务，正确的做法是**创建一个新的 Run（状态为 PENDING）**，而不是把旧 Run 的 `status` 硬改成 `pending`，否则其事件序列号和统计指标全部错位！
- ⚠️ **避坑 2：`Command` 对象的字段白名单约束**
  在接收外部恢复请求时，切忌把客户端传来的整个 JSON 字典一股脑作为 kwargs 喂给 `Command`。必须严格按白名单过滤（`graph`, `update`, `resume`, `goto`），防止恶意注入非法属性引发图执行引擎内部报错。
- ⚠️ **避坑 3：Thread 与 Run 状态的双向联动更新**
  当 Run 进入 `INTERRUPTED` 终态时，必须同时在同一事务内更新 `ThreadRow.status = "interrupted"` 并持久化 `ThreadRow.interrupts`，防止网关在查询 Thread 总体状态时出现不同步。

---

## 四、架构不变量清单（Architectural Invariants）

1. **有限状态转移闭包不变量（Finite State Machine Invariant）**：
   所有 Run 的状态变迁必须严格遵照 `run_state._ALLOWED` 转移矩阵，严禁任何未经验证的越级或逆向状态跳转。
2. **中断零资源驻留不变量（Stateless Interruption Invariant）**：
   任务一旦进入 `INTERRUPTED` 状态，必须彻底关闭并清理前台所有计算协程，释放租约，严禁在后台保持长轮询协程。
3. **终态物理不可逆不变量（Terminal Irreversibility Invariant）**：
   `SUCCESS`、`ERROR` 与 `TIMEOUT` 状态为绝对终态，一经持久化写入，该记录在数据库中严禁被再次修改。
