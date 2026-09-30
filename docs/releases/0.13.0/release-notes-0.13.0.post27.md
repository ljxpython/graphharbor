# 0.13.0.post27：官方框架 SDK 流协议与 Run 并发修复

日期：2026-09-11。`graphharbor` 与 `graphharbor-runtime` 已锁步发布到 PyPI；四份产物 SHA-256 与本地一致。

## 发布内容

- 执行端使用 LangGraph 公开 typed event stream，保留消息、工具调用、子图和审批事件；根 Run 终态仍在 Worker 提交执行状态之后发布。
- 官方框架 SDK 事件信封包含 `type=event`、消息稳定 ID、node/namespace、审批 payload；保留嵌套生命周期内容。
- state schema 使用真实 state schema，避免将 output schema 当成完整状态定义。
- 同 Thread 的 Run 提交加数据库行锁，`multitask_strategy=reject` 不再并发漏检；幂等键不能被复用于另一个 Thread/Assistant。
- REST 计数测试按创建前后增量核验，兼容部署图自动注册的默认 Assistant。

只发布通用执行引擎代码；未加入平台业务鉴权、消息队列表或业务 Middleware。使用用户授权的 PyPI token 直接上传构建产物，不创建 git 提交、tag 或 GitHub Release。

## 发布验证

- `uv lock --check`、`python3 scripts/check_versions.py`、Ruff format/check、mypy（36 个源文件）通过。
- 四份 wheel/sdist 构建成功，Twine 元数据检查通过；产物存于 `artifacts/release-post27/`，哈希存于同目录 `sha256.json`。
- PostgreSQL 测试使用独立 `graphharbor_release_post27` 数据库，不清空业务数据库。
- 首轮全库测试 148 passed、18 skipped、2 failed；缺少可选 DeepAgents 依赖及旧计数断言已处理；另修复 Python 3.11 fixture 的 TypedDict 来源，最终全库回归 **150 passed、18 skipped**（97.36 秒，Python 3.11）；跳过项未计为通过。
- Runtime 从本地 wheel 安装 post27（不使用源码路径）：五个真实 HTTP/浏览器消息场景全部通过（2.9 分钟），包括分批注入、两种取消窗口、撤权只读恢复、丢 ACK 后原请求重试。日志：`/var/folders/q6/4nvs05t90rg041hyp3_kws640000gn/T/q5-message-bh3jd7qz`。发布后从 PyPI 安装、更新 Runtime 锁文件并复验 **5 passed（2.5 分钟）**，日志 `/var/folders/q6/4nvs05t90rg041hyp3_kws640000gn/T/q5-message-_qj7z0v1`。

跳过的外部服务/模型对照测试不计入通过数；移动端覆盖为 Chromium 390×844 视口，不宣称真机矩阵。

## 已发布产物

- `graphharbor_runtime-0.13.0.post27-py3-none-any.whl`：`2058cedeb0e7bceba12e5a41d30ceb2e6e5511651ecbc9ca038b3dc8bb317fa3`
- `graphharbor_runtime-0.13.0.post27.tar.gz`：`75ee1869b56a8b47fb0d92ef20cff586e76a01ccc97a6c6140e799d50e4134a3`
- `graphharbor-0.13.0.post27-py3-none-any.whl`：`1de3f8dd34634fa3ef8598310b095f7303ce73bcbac079e4a00ec05d1add0197`
- `graphharbor-0.13.0.post27.tar.gz`：`b5b97b6984b32b2e8ee3b36f0420ab93215f546ca370bd2aae0475094f238e24`

2026-09-15：曾尝试的游标优化已因非零旧游标回归失败撤回，未发布。本轮通用子图协议修复与验证见 [post28说明](release-notes-0.13.0.post28.md)。
