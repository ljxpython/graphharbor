# GraphHarbor AI 经验库索引 (docs/lessons/)

> **读取规则：** 开始处理具体包或系统模块改动前，按需读取对应领域的经验文档。
> **沉淀规则：** 踩坑或纠错后，提炼为 **≤ 4 行** 的极简格式，严禁把整段长篇对话粘贴进来。

---

## 📚 领域经验文件清单

| 领域 / 模块 | 对应文件 | 沉淀条数 | 适用场景 |
|---|---|---|---|
| **ASGI 网关与协议** | [langhost-gateway.md](langhost-gateway.md) | 2 条 | CLI、Core REST API、SSE 流式、心跳保活、鉴权分发 |
| **运行时持久化与调度** | [runtime-persistence.md](runtime-persistence.md) | 3 条 | PostgreSQL 状态机、Lease 租约、Redis 队列、子智能体历史 |
| **AI 工作流与工程规范** | [ai-workflow.md](ai-workflow.md) | 3 条 | 破坏性测试、双包版本锁步、文档陈旧与假路径 |

---

## 📝 经验条目标准格式 (≤ 4 行)

```markdown
## [坑] 简明问题描述
- **场景：** 触发问题的具体业务或改动场景
- **错误：** AI 或旧代码犯过的典型错误做法
- **正确：** 经过验证的最佳实践与正确解法
- **日期：** YYYY-MM-DD
```
