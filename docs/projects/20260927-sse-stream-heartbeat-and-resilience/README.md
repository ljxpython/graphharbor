# SSE 事件流保活、确定性心跳与高频鉴权治理专项

## 项目状态

- **启动日期：** 2026-09-27（Asia/Shanghai）
- **目标：** 彻底解决 GraphHarbor 在通用 Agent Server 模式下 SSE 事件流与 Agent Protocol 流中的**心跳假死（过滤事件吞噬心跳计时器）**、**高频内存循环同步鉴权打爆下游**、**反向代理 Keep-Alive 缺失**以及**流队列资源潜在泄漏**问题。
- **级别：** 链路与治理改动；直接影响客户端与网关可见的 SSE/Protocol 传输层契约、连接生命周期、下游平台鉴权负载与内存队列回收。
- **状态：** 已完成（`done`），`0.13.0.post35` 已正式发布 PyPI 并在业务平台完成联调升级验证。
- **事实基线：**
  - GraphHarbor 锁步发布版本：`0.13.0.post35`；
  - 平台集成验证：`/Users/lijiaxin/PyCharmMiscProject/ai-agent-platform/apps/runtime-service` 已完成锁定升级至 `0.13.0.post35` 并重启服务栈验证；
  - 平台侧上游建议：
    - `graphharbor-upstream-recommendations.md`（心跳保活与鉴权暴击治理）；
    - `graphharbor-zombie-interrupt-replay-recommendations.md`（历史僵尸中断事件重放治理）；
  - 核心实施与测试：`libs/langhost/src/langhost/protocol_api.py`、`libs/langhost/src/langhost/streaming.py`、`libs/langhost/tests/test_stream_heartbeat_resilience.py`。

---

## 导航

- [详细技术方案与代码级改造 (plan.md)](plan.md)
- [分阶段实施任务清单 (tasks.md)](tasks.md)
- [严格验证与复现验收方案 (verification.md)](verification.md)

---

## 平台控诉事实核查（到底是不是这么回事？）

**结论：完全属实！不仅属实，真实病灶比平台控诉文档发现的还要严重且广泛！**

平台反馈的主要痛点是前端连接在 45 秒内收不到任何心跳而被前端强制判定为假死断开。经过对 GraphHarbor 源码的全面地毯式审查，确认存在以下确凿病灶：

| 平台反馈现象 | 源码真实成因 | 严重程度 | 影响范围 |
|---|---|---|---|
| **静默期 45 秒无心跳断连** | 伪超时机制：心跳计时器挂在 `wait_for(queue.get(), timeout=15)` 上。队列每来一个被过滤的事件（如 `seen` 重复事件、`channels` 不匹配事件、非目标 `modes` 事件），`wait_for` 成功返回并 `continue`，**时钟被硬生生偷走并重置**，导致 15 秒心跳永远无法触发。 | **致命** | `protocol_event_stream`、`thread_stream`、`runs_stream`（全部三套流式端点） |
| **高频同步 HTTP 鉴权请求风暴** | 内存循环查库/查权限：在 `for wire in replay` 和 `for row in rows` 的内存迭代中，每条消息都调用 `_thread` / `_get_thread`，每次都向平台 API 发起一次完整的 HTTP POST 鉴权回查（`/api/runtime/internal/thread-authorization`），回放 50 条消息就同步打 50 次远程 HTTP。 | **严重** | 单次连接建立延迟剧增、下游网关瞬时打满、长连接心跳时持续产生 HTTP 冲击 |
| **缺少 Keep-Alive 标头（隐蔽暗坑）** | `protocol_event_stream` 的响应头中只设置了 `Cache-Control` 和 `X-Accel-Buffering`，缺少 `Connection: keep-alive`，在经过 Nginx/Kong/Cloudflare 等反向代理时，易被代理层提前切断连接。 | **中等** | 反向代理与网关穿透场景 |
| **队列生命周期管理未完全保护** | 在 `add_thread_stream` 之后，部分早期校验与预取逻辑（如 `_resumable_run_ids`）未被包含在最外层的 `try...finally` 块中，一旦预取抛出数据库瞬时异常，队列对象残留在 manager 中永久泄露。 | **严重** | 长时间运行下的服务端内存泄漏 |

---

## 治理范围与原则

1. **确定性心跳调度（Deterministic Activity Heartbeat）**：
   心跳发射必须基于**“距离上一次向客户端实际输出任何数据的时间（`last_sent_at`）”**。队列被内部事件唤醒但决定过滤丢弃时，必须检查活动间隔；若已超过心跳周期，**立即补发心跳并重置计时**，严禁让过滤事件“吞掉”心跳。
2. **三流全面对齐（Full Stream Alignment）**：
   禁止只修复 `protocol_event_stream` 而遗留 `thread_stream` 和 `runs_stream`。三个流式入口统一应用相同的活动时钟模型。
3. **鉴权回查轻量防抖/缓存（Scoped Auth Throttle）**：
   长连接建立后，在流的生命周期内引入短时鉴权缓存（TTL 5~10 秒），或在进入流时做基线校验，心跳期间仅在必要间隔进行复核，彻底杜绝回放阶段的 N 次网络风暴。
4. **零破坏性（Zero Regression）**：
   严格兼容官方 Agent Server 协议规范、官方 SDK 以及下游 Platform API 的现有预期。
