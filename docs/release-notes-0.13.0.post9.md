# GraphHarbor 0.13.0.post9

## 新增

- 提供五个复杂 acceptance P0 fixture，覆盖 LangGraph fan-out/handoff、LangChain
  Agent、Deep Agents delegation、真实 DeepSeek tool loop。
- 提供固定版本 `langgraph dev` 的 Core 协议与 P0 双端结构化比较、MCP Streamable HTTP
  transport、能力映射校验和四象限兼容性快照比较。

## 修复

- Store HTTP handler 在 runtime 模块重载后解析当前 PostgreSQL Store，避免已启动 pool
  被旧模块引用误判为未初始化。

## 验证

- 本地 PostgreSQL/Redis 全量测试：`95 passed, 15 skipped`。
- 真实验收：`21/21 passed`；五个 P0 对固定 `langgraph dev` 差分为 0。
