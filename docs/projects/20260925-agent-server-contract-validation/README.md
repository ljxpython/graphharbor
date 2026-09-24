# Agent Server 契约验证与兼容门禁

## 项目概述
- **时间：** 2026-09-25 起
- **目标：** 修复 `POST /threads` 非法请求泄漏 500 的问题，并让 OpenAPI 请求、参数、响应和组件 schema 差异进入真实兼容门禁。
- **负责人：** 待指定
- **状态：** partial；输入校验和比较器门禁已完成，官方非法请求对照通过；全量 OpenAPI schema 仍有差异

## 阅读顺序
1. [Thread 请求输入验证](01-thread-request-validation.md)：定义边界输入、状态码和错误 envelope。
2. [OpenAPI schema 差异门禁](02-openapi-schema-diff-gate.md)：定义比较范围、规范化规则和 CI 阻断条件。

## 改动范围
- **影响服务：** `langhost` runtime API、协议比较脚本、契约测试和 CI。
- **改动级别：** 治理改动 + API 契约改动。
- **预计工作量：** 2-4 人天，取决于现有 OpenAPI schema 补齐范围。

## 关键决策
1. 所有信任边界输入先验证，验证失败返回稳定的客户端错误，不让 Python/Starlette 异常变成 500，也不在验证前触碰数据库。
2. 比较器递归比较契约结构；只规范化明确的动态值，不忽略 `required`、`type`、`format`、`enum`、状态码和 schema 引用等契约字段。
3. 官方固定版本和 OpenAPI 文件作为硬基线；任何排除项必须逐项登记，不能用“比较器通过”替代差异审查。
4. 优先复用现有 JSON/OpenAPI 结构和 Python 标准库，不新增依赖，不引入业务字段或租户逻辑。

## 完成定义
- 三类已复现非法 `POST /threads` 请求均返回约定 4xx，且数据库不可用时仍如此。
- 比较器对已知 `requestBody.required`、参数类型、响应 schema 和组件 schema 差异至少各有一个失败测试。
- 官方固定基线与当前实现的真实差异可读、可追踪；CI 不再把 `blocked`/`not_run` 当作 passed。
