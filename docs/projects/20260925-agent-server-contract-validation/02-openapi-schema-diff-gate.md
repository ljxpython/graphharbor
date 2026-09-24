# OpenAPI schema 差异门禁

## 目标
让官方协议比较器发现 requestBody、parameters、responses 和 components schemas 的真实契约差异，避免 operation 为空或主动跳过 `/openapi.json` 时仍报告零差异。

## 方案设计

当前 `libs/langhost/src/langhost/server.py:182` 手工生成路径并返回空 operation；`scripts/compare_official_protocol.py:345` 只比较 path/method，且 `compare()` 跳过 `/openapi.json` body。现有测试 `test_compare_openapi_uses_path_and_method_shape_only` 直接保护了这个缺陷。

比较器改为对两份 JSON 做递归结构比较，范围固定为：paths、methods、parameters、requestBody、responses、components.schemas，以及这些结构中的 `$ref`、`allOf`、`oneOf`、数组 items、required/default/enum/type/format/nullability 和 HTTP 状态码。优先使用标准库，不引入 jsonschema 依赖。

只规范化明确的非契约动态值，例如服务地址、时间戳、UUID 示例、描述文本或 operationId（需逐项确认并记录）；不得规范化 required、类型、格式、枚举、默认值、响应状态码或 schema 引用。已登记的 path/method exclusions 继续生效，但不得扩大成全局忽略 body。

OpenAPI 生成侧应逐步补齐可验证的 operation 内容，至少覆盖 `/threads` 及核心资源的请求体、参数、响应和组件 schema。不能把官方 JSON 原样复制后宣称实现；官方固定文件仅作为基线和差异来源。

## 任务拆分
- [x] Task 1：锁定官方版本、OpenAPI 文件 hash 和比较输入，记录基线变更流程。
- [x] Task 2：重写 `_compare_openapi()` 的递归差异输出，移除 `/openapi.json` body 跳过逻辑。
- [x] Task 3：修改/删除只比较 path/method 的旧测试，新增 requestBody 和 components schema 差异失败测试。
- [x] Task 4：补齐 `libs/langhost/src/langhost/server.py` 的 `/threads` 核心 operation/schema。
- [x] Task 5：保留并测试动态字段规范化与显式 exclusions，防止比较器因环境 URL 或文案变化产生噪声。
- [x] Task 6：将比较器与固定官方 OpenAPI hash 回归接入常规 CI；官方服务全量差分仍在 compatibility-upgrade workflow 中执行并对差异失败。

## 验证要求与记录
### 验证要求
- [ ] 官方和本地 `/threads` operation 仅 `requestBody.required` 不同即失败。
- [x] parameter 的 type/format/required 任一不同即失败。
- [x] response 状态码或 response schema 不同即失败。
- [ ] components schema 的 required/enum/$ref/嵌套 items 不同即失败。
- [x] 仅 description、operationId 或登记的动态 URL 不同可通过。
- [x] 明确 exclusions 只忽略指定 path/method，不能吞掉其它 schema 差异。
- [x] 当前官方基线与 GraphHarbor 输出能打印可读差异，而不是零差异假通过。
- [x] CI 对固定官方规范缺失或 hash 漂移，以及 schema 差异检测退化阻断；全量差分 workflow 对真实差异阻断。

### 验证记录
比较器单元测试已通过；官方固定版本对照和 CI job 结果待执行。

#### 2026-09-25 验证
- ✅ requestBody.required 差异会失败。
- ✅ components schema required 差异会失败。
- ✅ description 差异不会触发门禁。
- ✅ 11 项比较器测试通过。
- ✅ path-level parameters、response status/schema、嵌套 format/$ref 差异测试通过。
- ✅ 固定 `langgraph-api==0.13.0` OpenAPI hash 校验通过，并确认当前输出存在真实差异。
- ✅ 最终复核：本机固定规范对当前 GraphHarbor 输出检测到 203 处差异（按现有 core profile 排除后 194 处），未把这些差异标记为兼容通过。
- ✅ 常规 CI 已纳入相关测试；全量官方比较仍会因现存差异失败。

## 状态
partial（比较器和 CI 退化门禁完成；全量 operation schema 尚未补齐）
