# 02-通用 Agent Server 边界隔离与防业务侵蚀

> **概念定位与核心价值**：本专篇剖析 GraphHarbor 的核心立项哲学——**切斯特顿栅栏（Chesterton's Fence）在通用 Agent 运行时中的实战防御**。深度解密为什么系统在 [Migration 008](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/versions/008_remove_business_scope.py) 中不惜打破向前兼容，断然将 `tenant_id` 与 `project_id` 从核心数据表中彻底铲除，以及二次开发团队应如何在不侵蚀通用底座的前提下优雅实现业务扩展。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
市政自来水公司铺设的供水主管网，职责只有一个：**把符合纯净水标准的自来水高压输送到千家万户**。

如果某家火锅店老板觉得天天倒红油底料麻烦，跑去找水务局说：“你们在主管网管道里给我加个专用分支，直接帮我灌装红油底料行不行？” 
水务局局长要是脑子进水答应了，结果就是：整条街的居民打开水龙头流出来的全是一股牛油麻辣味，其他正常住户直接报警，整个城市供水系统彻底瘫痪！

**通用 Agent Server 就是自来水管网，各家公司的业务鉴权、用户计费、多租户组织架构就是红油底料。主管网里只能流纯水，红油底料只能在出水口（业务网关层或 Graph 内部）自己加！**

### 2. 软件工程演进痛点
在内部平台研发过程中，“业务侵蚀通用底座”是最常见、最具破坏力的架构腐败：
1. **短视的“顺手加个字段”**：为了实现多租户计费或部门权限，工程师往往顺手在 `threads`、`runs` 表上加一列 `company_id` 或 `tenant_id`，再顺手在 REST API 加个查询入参；
2. **协议契约被永久污染**：一旦加了特定字段，LangGraph 官方提供的 SDK（Python / TS SDK）、官方调试界面（LangSmith Studio）以及第三方 Agent 前端压根不认识这些私有字段；
3. **技术负债指数级爆发**：官方协议每迭代一次，你的私有补丁就得全部重新适配一次。最终底座既不再是标准通用的开源组件，又不是彻底重构的定制系统，变成谁也维护不动的半吊子僵尸代码。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：直接侵入核心模型与通用接口
class NaiveRunRow(Base):
    __tablename__ = "runs"
    run_id = Column(UUID, primary_key=True)
    thread_id = Column(UUID)
    # 致命伤：将特定业务平台的租户、项目、计费账号硬编码进通用底座表
    tenant_id = Column(String(64), nullable=False)
    project_id = Column(String(64), nullable=False)
    llm_vendor_model = Column(String(128))  # 违背通用原则，写死模型供应商

# ✅ 生产级落地方案 (GraphHarbor Production)：纯通用模型 + JSONB 弹性透传
class RunRow(Base):
    __tablename__ = "runs"
    run_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    thread_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # 优势 1：仅保留通用运行时概念 (状态、重试、租约、心跳、事件序列号)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # 优势 2：业务租户、追踪标识一律通过 JSONB 原生透传，配 GIN 倒排索引支持极速查询
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, server_default=text("'{}'::jsonb"))
    kwargs: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 历史决绝反思：Migration 008 剔除业务字段
在早期版本中，系统曾短暂存在过平台级业务列。架构团队在 [008_remove_business_scope.py](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/versions/008_remove_business_scope.py) 执行了坚决的去业务化清理：

```python
# libs/langgraph-runtime-pg/src/langgraph_runtime_pg/migrations/versions/008_remove_business_scope.py
def upgrade() -> None:
    connection = op.get_bind()
    # 强制安全门禁：迁移前必须为空库或已完成数据备份迁移
    for table in ("assistants", "threads", "runs", "crons"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError(f"{table} must be empty before removing business scope")
            
    # 坚决铲除侵蚀通用底座的业务索引与物理列
    for table in ("assistants", "threads", "runs", "crons"):
        if table == "runs":
            op.drop_index("ix_runs_scope_status", table_name=table)
            op.drop_index("uq_runs_scope_idempotency", table_name=table)
        if table == "crons":
            op.drop_index("ix_crons_scope_enabled", table_name=table)
        for column in ("tenant_id", "project_id"):
            op.drop_column(table, column)
            
    # 重建纯净且高效的通用幂等唯一索引
    op.create_index(
        "uq_runs_idempotency",
        "runs",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
```

### 2. GIN 倒排索引支撑业务元数据弹性检索
查阅 [`models.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/models.py) 可以发现，既要通用性，又要业务检索性能，GraphHarbor 的解决方案是：**在通用表上对 `metadata` 列建立 PostgreSQL GIN (jsonb_path_ops) 倒排索引**：

```python
class ThreadRow(Base):
    __tablename__ = "threads"
    __table_args__ = (
        Index(
            "ix_threads_metadata_gin",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
    )
    # ...
```
这使得业务调用方可以随时通过 `{"metadata": {"tenant_id": "org_123", "env": "prod"}}` 查询 Thread 或 Run，而数据库引擎能直接走底层 GIN 倒排索引实现微秒级定位，完全无需在表结构上新增物理列！

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：不改核心表，二次开发怎么搞多租户鉴权与计费？
> **老王反问**：“谁告诉你多租户一定要在通用表里建外键的？你当网关中间件和反向代理是空气吗？！”
> 
> 工业级多租户与计费的正确解法有三层，层层解耦：
> 1. **接入网关层拦截（Platform API / Reverse Proxy）**：
>    上层业务网关负责校验用户 JWT，验证用户在业务组织中的权限与配额。验证通过后，将用户身份映射为通用 `Principal`，并透传给 GraphHarbor；
> 2. **元数据无缝下沉（Metadata & Config）**：
>    发起 Run 请求时，将业务租户标记通过标准 `config={"configurable": {"tenant_id": "xxx"}}` 和 `metadata={"tenant_id": "xxx"}` 传递；
> 3. **图内部感知与计费（In-Graph Hooks）**：
>    在 LangGraph 的图节点（Node）或自定义 Tool 内部，通过 `RunnableConfig` 直接读取 `configurable["tenant_id"]`，在执行 LLM 调用时打点上报业务计费系统，底层 Server 根本不需要关心这笔账怎么算！

### 2. 工业级避坑清单
- ⚠️ **避坑 1：严禁向 Core API 的 Pydantic Schema 强行加业务入参**
  修改 `core_api.py` 的端点入参会导致官方客户端发起的标准请求（无法感知你的私有入参）直接触发 422 Unprocessable Entity 校验失败！
- ⚠️ **避坑 2：业务查询优先使用 GIN 包含操作符 (`@>`)**
  在做多租户查询二次开发时，严禁使用慢速的 `metadata::text LIKE '%xxx%'`。必须使用 SQLAlchemy 的 `ThreadRow.metadata_.contains({"tenant_id": tenant_id})`，以命中 GIN 索引。
- ⚠️ **避坑 3：严禁把敏感 API 秘钥明文存入通用数据库**
  大模型 API Key（如 OpenAI Key、Anthropic Key）严禁作为明文字段写入 `threads` 或 `assistants` 的 `config` 中落库。应通过环境注入或接入外部 Vault/KMS 短时动态换取。

---

## 四、架构不变量清单（Architectural Invariants）

1. **核心模型纯净性原则（Model Purity Invariant）**：
   `models.py` 中的表模型只允许存在图执行、状态持久化、分布式租约与序列化相关的技术字段，绝不允许添加任何上层特定业务属性。
2. **官方契约等价性原则（Official API Parity Invariant）**：
   GraphHarbor 暴露给外部的所有公开 REST 与 SSE 接口，必须与 LangGraph 官方 Agent Server 规范保持 100% 契约兼容，不增加、不修改必填字段。
3. **业务上下文透明透传原则（Opaque Context Invariant）**：
   所有业务定制上下文必须通过通用 `metadata` 或 `config` 容器原样透传与存储，通用底座不对业务元数据的内部结构做任何强类型假设。
