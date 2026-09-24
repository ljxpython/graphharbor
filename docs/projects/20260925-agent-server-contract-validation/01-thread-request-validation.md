# Thread 请求输入验证

## 目标
让 `POST /threads` 在数据库访问之前完成请求解析和契约验证。非法 JSON、非 object、非法 UUID 以及字段类型或枚举错误都返回稳定的 4xx；合法请求保持现有创建流程和业务无关边界。

## 方案设计

当前 `libs/langhost/src/langhost/core_api.py:601` 直接调用 `request.json()`，随后把 `thread_id` 强转 UUID、把 `metadata` 强转 dict。解析异常、`KeyError`、`ValueError` 和类型错误因此泄漏为 500，且错误行为依赖数据库是否可用。

在 Thread handler 边界增加最小专用解析/验证 helper（或复用现有 body validator，先检索确认），按固定顺序执行：

1. 解析 JSON；解析失败返回与官方固定版本一致的 400/422，并使用项目现有错误 envelope 和 headers。
2. body 必须是 JSON object；数组、`null`、标量返回 422。
3. `thread_id`（存在时）必须是合法 UUID；缺省值沿用现有服务生成逻辑。
4. `metadata` 必须是 object；`config` 必须是 object。
5. `if_exists` 仅接受官方枚举 `raise`、`do_nothing`。
6. `ttl`、`supersteps` 按官方固定 OpenAPI schema 验证，至少先拒绝明显错误的 JSON 类型，再补齐嵌套字段约束。
7. 验证完成后才调用数据库和既有创建逻辑。

验证层只承载 LangGraph 通用协议字段，不加入模型供应商、业务工具、tenant/project 或 trace 规则。不要把数据库异常伪装成输入错误。

## 任务拆分
- [x] Task 1：确认官方 `langgraph-api==0.13.0` 对三类已复现非法输入的状态码、错误 envelope 和 headers。
- [x] Task 2：在 `libs/langhost/src/langhost/core_api.py` 增加边界解析/验证，确保任何验证失败早于数据库访问。
- [x] Task 3：补充无数据库 ASGI 测试，覆盖 malformed JSON、数组、`null`、非法 UUID、metadata/config 类型、非法 enum、ttl/supersteps 类型。
- [x] Task 4：用固定官方服务核对三类已复现非法输入的 status、content-type 和错误 envelope；本地无数据库测试锁定相同状态/envelope。
- [ ] Task 5：检查其它创建入口是否绕过 helper，并更新相关契约文档。

## 验证要求与记录
### 验证要求
- [ ] `POST /threads {"thread_id":"not-a-uuid"}` 返回约定 4xx。
- [ ] `POST /threads []` 返回约定 4xx。
- [ ] `POST /threads {"metadata":3}` 返回约定 4xx。
- [ ] 上述请求在数据库未配置/不可用时仍不返回 500。
- [ ] 合法请求在 fake/in-memory backend 下保持成功路径。
- [ ] 错误响应不泄漏 Python traceback 或业务内部异常。

### 验证记录
本地 ASGI 回归和固定官方服务对照已执行。

#### 2026-09-25 验证
- ✅ `libs/langhost/tests/test_cli.py`：非法输入在无数据库探测下返回 4xx，19 项组合测试通过。
- ✅ malformed JSON 返回 400；其余非法类型/枚举返回 422。
- ✅ 官方 `langgraph dev`（`langgraph-api==0.13.0`）对非法 UUID、数组 body、错误 metadata 类型均返回 422，`content-type: application/json`，body 为 `{"detail": string}`。

## 状态
进行中
