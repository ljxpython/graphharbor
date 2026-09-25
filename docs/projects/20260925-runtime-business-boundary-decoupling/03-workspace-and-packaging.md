# 03 Workspace 与发行包边界

## 目标

GraphHarbor 核心发行包不提供 DeepAgent 工作目录、业务 skills 路径、成果和 terminal 策略；平台现有工作区隔离及历史文件保持可用。

## 方案设计

### 已有实现

- G `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/deepagent_workspace.py` 仍定义 DeepAgentWorkspace、build_deepagent_workspace、resolve_workspace_virtual_path、resolve_skill_sources。
- G `libs/langgraph-runtime-pg/pyproject.toml` 的 wheel packages 指向整个 src/langgraph_runtime_pg。仅从 _MODULES 移除不能证明文件不进入发行包；最终须实际检查 wheel。
- P `apps/runtime-service/src/runtime_service/workspace/deepagent.py` 已有平台实现；workspace_demo、backend_demo 与 workspace policy 使用它。
- P 同目录 scoped.py 的 thread_scope_hash 基于 tenant/project/thread；这是平台正确的业务隔离，不应为了 GraphHarbor 通用化删除。
- P `runtime/resource_bindings.py`、DearFlow MCP 等依赖 `__graphharbor_thread_metadata` 读取已存储线程的绑定。

### 最小改动

1. 清点源码、测试、示例、依赖包导入；平台现役消费者统一使用现有 workspace/deepagent.py，不新建共享 workspace 包。
2. 比较两边路径穿越、绝对路径、symlink、skills 来源等安全测试，缺什么补什么；不要机械复制两份实现。
3. 发布迁移说明后，从 GraphHarbor 生产包移除 deepagent_workspace 实现和专属测试，平台保留相关安全覆盖。记录破坏性变更并通知消费者升级；不因未知外部消费者保留核心旧模块或导入别名。
4. wheel/sdist 实际构建后检查成员、import 与依赖，不让备份源文件（如 auth.py.backup）或旧平台模块漏进包。只清理本专题确认的发行污染，不借机扫除全仓库文件。
5. 平台 workspace roots、thread_scope_hash、现有文件路径不变；此任务无需移动用户文件或批量重新哈希。

### 哪些保留

- `libs/langgraph-runtime-pg/src/langgraph_runtime_pg/thread_config.py::attach_thread_metadata` 是通用“服务端线程 metadata 快照”能力，本身不解释 DeepAgent/workspace。暂保留并记录为 GraphHarbor 扩展，不冒充官方公共字段。
- 该快照必须来源于已授权的数据库 thread，客户端 config/metadata 同名键不可覆盖；响应/SSE 的脱敏规则继续有效。
- runtime_resource_bindings、workspace 策略与路径规则仍由平台验证。若将来有等价官方上下文入口，再迁移这个通用扩展，不为本次解耦临时另造 context 服务。
- 平台对通用 checkpointer、队列接口的引用不等于业务耦合，按功能契约保留。
- 自定义 HTTP app、graph factory 装配和运行用户 graph 的能力仍属于通用服务器；平台 /internal/... workspace/terminal/artifact 路由留在 runtime-service。

## 任务拆分

| ID | 改动内容 / 代码位置 | 预期结果 | 验证项 | 状态 |
|---|---|---|---|---|
| C01 | 两仓 workspace imports、示例、test_deepagent_workspace.py、平台 workspace tests | 消费者清单和安全覆盖差异；平台无需搬工作区 | V-C01、V-C02 | 阶段完成：两仓可控源码无 GraphHarbor workspace import；平台保留其 workspace 实现，定向安全测试通过 |
| C02 | P workspace/deepagent.py、scoped.py、resource_bindings.py、相关 tests | 缺失安全覆盖补齐；可信 metadata 约束明确 | V-C02—C04 | 部分完成：thread workspace 隔离、资源绑定、文件浏览、HTTP、zip、terminal 定向测试共 54 passed；Showcase/DearFlow 完整浏览器工作流与 restart/HITL/fork 联合恢复仍待验 |
| C03 | G deepagent_workspace.py、pyproject 打包、专属测试/文档、迁移说明 | 发行包无业务 workspace 实现，平台正常使用 | V-C01、V-C05 | 候选 wheel/sdist 构建并在临时目录安装；平台三份真实配置各加载 65 条路由，workspace 定向 23 passed。完整浏览器文件链路仍待验 |

## 验证要求与记录

- [x] V-C01：两仓源码和可控消费者无 GraphHarbor workspace import；破坏性删除与平台升级说明有记录，不保留旧模块兼容。复查 GraphHarbor `libs`/`tests` 无 workspace 导入，平台 workspace 消费留在 `runtime-service`。
- [ ] V-C02：路径穿越、绝对路径、symlink 逃逸、非法组件、skills 来源、已有文件读写；跨 tenant/project/thread 均隔离。
- [ ] V-C03：伪造 __graphharbor_thread_metadata/runtime_resource_bindings 无效；可信历史绑定可在 restart、HITL、fork 后按原策略解析。
- [ ] V-C04：Showcase 与 DearFlow 文件树、创建、预览、下载、zip、terminal、skills、fork/workspace 重连走既有授权；旧路径可读，不发生文件迁移。
- [ ] V-C05：wheel/sdist 成员与干净安装/import、平台从候选 wheel 启动通过；浏览器文件链路仍未运行。

复用 G test_deepagent_workspace.py 中安全案例；P test_thread_workspace_isolation.py、test_workspace_http.py、test_workspace_zip.py、runtime/test_resource_bindings.py、services/test_workspace_policy.py、test_terminal_http.py 与平台网关 workspace/files tests。Final 加一条真实浏览器创建文件→预览→下载验收，不要求真实模型作为路径安全基线。

**2026-09-25 调研记录：** 平台本地 workspace 模块与 GraphHarbor 残留源文件已核对；尚未构建候选 wheel、执行文件或浏览器测试。没有移动用户 workspace。

**2026-09-25 实施记录：** GraphHarbor 已删除 `deepagent_workspace.py` 及专属测试，源码无可控消费者；平台 workspace 保留。Runtime Service workspace 定向测试 54 passed。两包 wheel/sdist 在临时目录构建成功；`graphharbor_runtime` wheel 与 sdist 成员均不含 `deepagent_workspace.py`。尚未在干净环境安装 wheel，未运行浏览器链路。

## 状态

**2026-09-25 候选包复核：** `uv build --all-packages --out-dir /tmp/graphharbor-boundary-candidate-20260925` 构建双 wheel/sdist；`uv pip install --target /tmp/graphharbor-boundary-candidate-install-20260925 --no-deps` 后，平台 `.venv` 通过 `PYTHONPATH` 从候选包导入、加载真实 `langgraph.json`。`deepagent_workspace` 不可导入，`ThreadRow` 无业务 scope 列；平台 workspace 六组定向用例 23 passed；Web session/workspace/files/preview 六组 Vitest 31 passed。浏览器创建/下载、真实文件恢复仍未运行。

**2026-09-25 最终候选复核：** 双 wheel/sdist 安装在 `/tmp/graphharbor-boundary-final-install-20260925`；成员无 DeepAgent workspace、备份源码或平台模块。平台三份配置各加载 65 条路由，候选 Runtime 从安装包在临时端口启动并完成 Thread HTTP 链路；文件浏览器链路未运行。

**2026-09-25 本机候选启动：** 最新双 wheel 已安装到平台 Runtime 虚拟环境，平台本机栈健康。平台 `uv.lock` 仍锁旧 PyPI wheel，候选启动需要 `UV_NO_SYNC=1`；这不是可重复安装的正式交付。文件浏览器正向操作与 workspace 恢复仍未验证。

partial：C01 阶段完成，C03 候选包安装/平台加载通过；C02 平台浏览器文件正向链路已通过，重启/HITL/fork 资源绑定及完整安全矩阵仍未完成。不能替代 01/04 的安全与迁移准入。

2026-09-25 正式 post33 联调：在普通 `local-stack.sh restart` 启动的本机平台栈运行 `pnpm exec playwright test e2e/retired-result-service.spec.ts --project=chromium --reporter=line --workers=1`，1 passed（1.7 分钟）。该用例经浏览器创建 Dear Agent 会话，模型调用 `write_file` 写入 `/workspace/work/retirement-check.md`，经审批和 `present_artifacts` 发布后，在成果页完成在线预览与安全下载，并清理测试 Thread/项目。模型来自平台当时已配置且可用的项目模型；`miaomiaoai` 的真实模型独立烟测见 04。此证据覆盖文件正向链路，不覆盖跨租户路径逃逸、重启/HITL/fork 恢复或 zip/terminal/skills。

2026-09-26 本地执行后端复测：Runtime Service workspace/zip/HTTP/browser/terminal/resource-binding 定向回归 54 passed；独立 PG17 的 DearFlow restart + HITL/workspace 1 passed。skill snapshot restart 在本地后端使用现有 `RUNTIME_SKILLS_ROOT`、`RUNTIME_WORKSPACE_ROOT` 后 4 passed。原失败是测试硬编码 Docker `/skills`、`/workspace` 路径；本次只修正平台测试脚本，未改变 GraphHarbor 或 workspace 实现。完整浏览器工作流和跨租户 Final 仍未完成。
