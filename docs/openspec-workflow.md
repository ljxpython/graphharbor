# OpenSpec 工作流

仓库已初始化 OpenSpec `1.6.0` 的 `spec-driven` 配置，变更目录位于
`openspec/changes/`。本地需要在 PATH 中提供 `openspec` CLI；检查安装：

```bash
openspec --version
openspec doctor --json
```

当前生产重写变更：

```bash
openspec status --change implement-production-agent-server --json
openspec instructions apply --change implement-production-agent-server --json
openspec validate implement-production-agent-server --strict
```

流程固定为 `proposal -> specs -> design -> tasks -> apply -> verify -> archive`。生产协议、认证、
状态机、迁移和发布门禁必须先更新对应变更，再修改代码；不能把勾选任务当成验证证据。
