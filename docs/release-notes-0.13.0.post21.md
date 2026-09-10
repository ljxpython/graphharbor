# 0.13.0.post21：Context 与持久化状态修复

发布日期：2026-09-10。`graphharbor`、`graphharbor-runtime` 已锁步发布到 PyPI。

## 修复

- 执行适配通过 LangGraph 官方 `context=` 参数传入运行 Context，覆盖 invoke、stream 及审批恢复。
- 状态及历史接口使用 `aget_state/aget_state_history` 还原增量通道、pending tasks 与 interrupts，避免 DeepAgents messages 丢失。
- Worker 在合法运行上下文下保存实际 graph ID，普通创建的 Thread 也能正确还原状态。

## 发布范围与来源

以 PyPI `0.13.0.post20` 的两份官方源码包为基线，在隔离目录加入上述修复和测试；没有打包工作区中的其他未提交架构修改。源码包下载哈希已核对，发布后四份产物的 PyPI SHA-256 与本地一致。
初次打包继承了旧 sdist 的 PKG-INFO，上传工具在本地校验阶段拒绝；清除临时目录中的旧元数据后重建，两个 wheel 哈希保持不变，源码包各含一个 PKG-INFO 后才成功上传。

## 验证

- `uv lock --check`、`scripts/check_versions.py`、Ruff lint/format、mypy（36 个源文件）通过。
- 隔离发布目录全部 tests：145 passed、15 skipped（217.71 秒）；跳过项依赖缺失的上游对照服务/fixture，不算通过。
- 首轮隔离环境缺少 acceptance 依赖和仓库比较脚本；补齐后相关 27 项及最终全套回归通过。
- 最终 wheel + 正式鉴权 + PostgreSQL/Redis + 真实模型：API/Worker 重启后恢复原审批及完整 messages，修复 CSV 报表并运行 3 项检查，输出 43.50，真实 Docker 退出码 0。
- Runtime 教学 Demo 验收记录：ai-agent-platform 仓库 `docs/projects/20260908-showcase-demo/verification.md`。

## 产物 SHA-256

- `graphharbor-0.13.0.post21-py3-none-any.whl`：`0d2dd75f634f9b61fff4ec66d2436c614b51631e11a84fb297c6798c7f92a0ed`
- `graphharbor-0.13.0.post21.tar.gz`：`8eee6888b4e7cc398d08e4f82c458f3db85bd05844abb1ef08333498d20cf405`
- `graphharbor_runtime-0.13.0.post21-py3-none-any.whl`：`06b038cc029e482ed8c2d9a59558fca5616e2be5719a0f0790c7138c79885446`
- `graphharbor_runtime-0.13.0.post21.tar.gz`：`01002f072a96c7dd61a795dd5590d00aee4446aec413fc548a7918f6b0a16160`

发布后 runtime-service 通过 `uv sync --frozen` 从 PyPI 安装 post21（无临时源码覆盖），回归 152 passed；再次通过真实模型/API/Worker 重启恢复，Thread `bf964ad4-bc31-4097-8c9b-33d9d9989d5a` 最终 success，独立检查输出 43.50、退出码 0。
