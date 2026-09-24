# 契约输入校验与 OpenAPI 门禁实现

## 改动时间
2026-09-25

## 相关任务
- 01 Task 2-4：Thread 创建边界验证与无数据库回归测试
- 02 Task 2-4：OpenAPI 递归契约差异、测试和 `/threads` schema

## 改动文件
- `libs/langhost/src/langhost/core_api.py`
- `libs/langhost/src/langhost/server.py`
- `scripts/compare_official_protocol.py`
- `libs/langhost/tests/test_cli.py`
- `libs/langhost/tests/test_official_protocol_compare.py`

## 具体改动

### 1. Thread 输入边界
`threads_create()` 现在先通过 `_thread_create_payload()` 解析并验证 JSON。非法 JSON 返回 400；非 object、UUID、metadata/config、if_exists、ttl、supersteps 类型或枚举错误返回 422，验证通过后才连接数据库。

### 2. OpenAPI 比较
比较器不再只比较 path/method。它现在递归比较 operation 的 `parameters`、`requestBody`、`responses`、`security` 和 `components.schemas`，显式 path/method 排除仍然有效；描述和 operationId 等非契约字段不会触发差异。

### 3. OpenAPI 生成
补充 `/threads` POST 的 requestBody、responses 以及 `ThreadCreate`、`Thread` 组件 schema，避免该 operation 继续以空对象表示。

## 验证
- [x] `uv run pytest libs/langhost/tests/test_cli.py libs/langhost/tests/test_official_protocol_compare.py -q`：19 passed
- [x] `uv run ruff check ...`：通过
- [x] 固定官方 OpenAPI hash 校验和真实差异检测：通过，最终复核：未排除 203 处，按现有 core profile 排除后 194 处差异
- [x] 官方服务对照：在 fixture 目录启动 `langgraph dev`，三类非法请求均为 422 + JSON detail
- [ ] 全量官方 OpenAPI 一致性：当前仍有大量真实差异，不能标记通过

## 已知边界
当前只补齐 `/threads` 的核心 OpenAPI schema；其余路径仍有空 operation，需要后续逐个补齐，比较器会如实暴露这些差异。
