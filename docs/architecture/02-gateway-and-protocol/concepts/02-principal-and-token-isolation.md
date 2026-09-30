# 02-Token 校验、Principal 上下文与线程所有权隔离

> **概念定位与核心价值**：本专篇深度解密 GraphHarbor 的**零信任安全接入架构与跨租户防越权（IDOR）防护体系**。详述系统如何通过 `PrincipalMiddleware` 中间件、不可变 `Principal` 身份上下文、以及基于 HMAC-SHA256 签名的任务执行委托凭证，从根源上杜绝权限混淆、未授权数据穿透与跨租户幂等键碰撞风险。

---

## 零、痛点与生活演进史（Why）

### 1. 生活大白话演进史
隔壁老李去一家高端洗浴中心泡澡：
- 进门实名认证后，前台发给他一个**带加密芯片的专属智能手环**（`Principal`，编码 `user_laoli_88`）；
- 老李拿着手环，只能刷开自己的 88 号更衣柜。隔壁 89 号衣柜里放着别人价值几十万的劳力士金表；
- 如果老李心怀不轨，拿记号笔在手环表面画了个“89”，跑到 89 号柜子前刷，柜门的芯片读头校验数字签名不通过，当场警报大作保安冲出（IDOR 越权拦截）；
- 更严密的是：洗浴中心维修电工手里有一把“机房总配电箱总钥匙”（`X-GraphHarbor-Management-Key`）。这把钥匙如果被拿去尝试开客人的更衣柜，锁头感应到这是机房钥匙，**同样当场拒绝开锁**（管理面与数据面物理隔离）！

### 2. 软件工程演进痛点
在智能体平台工程化落地时，以下三类安全事故最让安全总监彻夜难眠：
1. **水平越权漏洞（IDOR）**：网关只验证了用户传来的 JWT 是合法的，但控制器直接拿入参的 `thread_id` 查库。攻击者只要登录自己的账号，然后写脚本遍历 UUID，就能把全公司甚至全平台其他客户的商业私密问答全部拖库！
2. **凭据权限混淆（Privilege Confusion）**：运维平台或 CI/CD 使用的超级管理员秘钥，被开发者偷懒拿来调用普通聊天接口。一旦管理秘钥在前端网络传输中泄露，黑客直接获得全库越权读写能力；
3. **跨租户幂等键碰撞（Idempotency Key Collisions）**：两个不同的企业租户在重试请求时碰巧使用了相同的键（例如 `idempotency_key: "batch_task_01"`）。如果系统不做多租户加盐隔离，后一个租户的任务会直接被判定为“已存在”，导致严重的数据串号与业务卡死。

---

## 一、对立视角：20 行极简对立代码（Naive vs Production）

```python
# ❌ 简易原型方案 (Naive)：只校验 Token 有效性，不校验数据归属，毫无防越权能力
@app.get("/threads/{thread_id}/history")
async def naive_get_history(thread_id: str, request: Request):
    user = verify_jwt(request.headers.get("Authorization"))
    # 致命伤：只要用户有合法 Token 就放行，根本不核对这个 thread 到底属于谁！
    # 攻击者传别人的 thread_id，直接把别人的商业机密聊天记录读得一干二净 (IDOR 漏洞)
    return await db.fetch_all("SELECT * FROM checkpoints WHERE thread_id = %s", thread_id)

# ✅ 生产级落地方案 (GraphHarbor Production)：Principal 上下文 + 行级隔离 + 加盐哈希
class PrincipalMiddleware:
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        # 核心防守 1：严禁管理面 Key 穿透数据面
        if b"x-graphharbor-management-key" in dict(scope.get("headers", [])):
            return await json_error(send, 403, "Management credentials cannot access data-plane")
        # 核心防守 2：提取并注入不可变 Principal 安全上下文
        user = await self.auth_handler.authenticate(scope, ...)
        scope["principal"] = Principal.from_auth_user(user)
        await self.app(scope, receive, send)

# 业务控制器内部执行严格所有权核验
async def threads_history(request: Request):
    principal = principal_from_scope(request.scope)
    thread = await fetch_thread(request.path_params["thread_id"])
    if not in_principal_scope(thread, principal):
        raise HTTPException(403, "Forbidden: access denied to this thread")
```

---

## 二、真实工程代码全景剖析（Real Engineering Code）

### 1. 不可变主体数据类：`Principal`
查阅 [`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py)，系统定义了完全冻结不可变的 [`Principal`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py) 结构：

```python
@dataclass(frozen=True, slots=True)
class Principal:
    subject: str                      # 权威身份唯一标识 (如 usr_123)
    roles: frozenset[str] = frozenset()
    scopes: frozenset[str] = frozenset()
    credential_type: str = "custom_auth"
    jti: str = ""
    claims: dict[str, Any] | None = None
    auth_user: dict[str, Any] | None = None
    request_id: str | None = None

    def idempotency_key(self, key: str | None) -> str | None:
        """Bind client retry keys to the authenticated generic identity."""
        if not key:
            return None
        # 强行以 subject 为盐进行 SHA-256 计算，阻断跨用户键碰撞
        return "auth:" + sha256(f"{self.subject}\0{key}".encode()).hexdigest()
```

### 2. 管理面凭据阻断：物理隔离红线
查阅 [`auth.py`](file:///Users/lijiaxin/PyCharmMiscProject/graphharbor/libs/langgraph-runtime-pg/src/langgraph_runtime_pg/auth.py) 第 394~401 行，系统在最外层中间件直接扼杀凭证混淆：

```python
management_header = headers.get(b"x-graphharbor-management-key", b"").decode("latin-1")
if management_header:
    # 核心守卫：管理面凭证绝对禁止访问数据面
    await _json_error(
        send, 403, "management credentials cannot access data-plane resources"
    )
    return
```

### 3. HMAC-SHA256 签发与验证执行上下文（Runtime Context）
当网关把任务派发给后台异步 Worker 时，不能直接把客户端传来的明文 HTTP Header 透传过去，否则 Worker 无法证实请求中途是否被篡改。

系统使用 `sign_runtime_context`，利用专有秘钥 `GRAPHHARBOR_RUNTIME_CONTEXT_SECRET` 进行签名：
```python
claims = {
    "v": 2,
    "accepted_at": accepted_at,
    "run_id": str(run_id),
    "thread_id": str(thread_id),
    "context": {
        "auth_user": {"identity": principal.subject, ...},
        "permissions": list(principal.scopes),
    },
    "iss": "graphharbor-gateway",
    "aud": "graphharbor-worker",
}
signature = hmac.new(secret, encoded_claims.encode(), hashlib.sha256).hexdigest()
token = f"{encoded_claims}.{signature}"
```
Worker 在消费队列任务时调用 `verify_runtime_context`，一旦签名不匹配或有效时间超期，当场拒绝执行！

---

## 三、老王灵魂拷问与工业级避坑指南（Engineering Pitfalls）

### 1. 灵魂拷问：为什么自定义 `auth_handler` 绝不能提前读取 Request Body？
> **老王暴躁拍桌**：“在 ASGI 中间件里直接 `await request.json()` 的，十个有九个把系统搞崩！”
> 
> 在异步 ASGI 协议中，请求体数据流（Request Body Stream）是通过 `receive` 协程按 Chunk 传输的，**而且默认只能被读取一次（Single-shot Stream）**！
> - 如果你的 `auth_handler` 为了校验某个签名字段，自作聪明把 Request Body 给读了；
> - 下游 Starlette 路由和控制器（如 `core_api.runs_create_thread`）再次尝试读取 JSON 时，拿到的就是一个空字节流，直接触发 400 Bad Request 崩溃！
> - **GraphHarbor 标准姿势**：查阅 `authenticate_with_auth_handler`，系统只向认证函数传递 `scope`, `headers`, `path_params` 和 `query_params`，坚决不消耗 `receive` 流，保证下游业务完整读取。

### 2. 工业级避坑清单
- ⚠️ **避坑 1：生产环境必须设置 `GRAPHHARBOR_RUNTIME_CONTEXT_SECRET`**
  如果在生产环境（`GRAPHHARBOR_ENV=production`）没有配置该秘钥，系统在派发异步 Run 时会直接抛出 `RuntimeContextError`。二次开发团队部署上线前必须通过环境变量注入高强度随机秘钥。
- ⚠️ **避坑 2：开发环境与生产环境安全行为对齐**
  在开发环境下，为了方便 Postman 与本地单测调试，`allow_anonymous=True` 允许不带 Header 访问；但在生产环境会自动收紧为 `False`。切忌把包含未鉴权绕过的测试代码合并到主干！
- ⚠️ **避坑 3：健康探针白名单不得随意扩大**
  `/ok`, `/live`, `/ready`, `/info` 是仅有的公开探活接口。绝不允许二开人员为了省事，把 `/assistants` 或 `/threads` 加入到未鉴权白名单集合中！

---

## 四、架构不变量清单（Architectural Invariants）

1. **不可变主体单向流转不变量（Immutable Principal Invariant）**：
   `Principal` 一旦在中间件中构建并存入 `scope["principal"]`，其内部字段必须全局冻结，严禁在后续中间件或控制器中动态覆写其身份。
2. **跨租户强加盐隔离不变量（Salted Tenant Isolation Invariant）**：
   所有具有幂等性保证的业务键值，必须与当前 `Principal.subject` 执行加盐哈希，杜绝任何全局未加盐的原始键存库。
3. **控制面与数据面强边界不变量（Plane Separation Invariant）**：
   管理面秘钥严禁访问数据面资源，数据面 Token 严禁访问管理面接口，两者在协议与网关层实现物理断路。
