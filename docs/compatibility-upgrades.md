# LangGraph 兼容升级

GraphHarbor 的公开 HTTP、SSE 和官方 SDK 可见输出以固定版本官方 `langgraph dev` 为准。项目不自动检查或升级上游；只有主动修改 `langgraph`、`langgraph-sdk`、`langgraph-cli`、checkpoint 或 `langgraph-api` compatibility 版本时，才执行本流程。

1. 在升级分支更新依赖和 `uv.lock`，但不要更新发布兼容映射。
2. 手动触发 GitHub Actions `Compatibility Upgrade`，`official_version` 填待验证的官方版本。
3. workflow 启动同配置的官方 `langgraph dev` 与 `graphharbor serve`，再运行：

   ```bash
   uv run python scripts/compare_official_protocol.py \
     --official-url http://127.0.0.1:31398 \
     --graphharbor-url http://127.0.0.1:31397
   ```

4. 若输出有差异，先调整 GraphHarbor；UUID、时间戳、生成位置 header 是唯一允许归一化的动态字段。OpenAPI 只比较路径和方法，操作描述由各框架自行生成。工作流失败时下载两端日志定位差异。
5. Python/JavaScript SDK、REST/SSE、持久化和 P0 graph 门禁均通过后，更新 `docs/compatibility-matrix.json` 和 `docs/compatibility-matrix.md`，再发布 GraphHarbor。

统一验收命令（本地 PostgreSQL/Redis，不依赖 Docker）：

```bash
uv run python tests/acceptance_app/run_acceptance.py \
  --base-url http://127.0.0.1:31296 \
  --official-url http://127.0.0.1:31398 \
  --require-official \
  --with-chat --with-agents --with-javascript --with-p0 --with-mcp --with-network-sse
```

`--with-mcp` 前先启动本地 fixture：

```bash
uv run --group acceptance python tests/acceptance_app/mcp_server.py
```

GitHub Actions 的 `Compatibility Upgrade` 提供 `run_real_acceptance` 手动输入。启用后使用
仓库 secrets 中的 DeepSeek 配置，另起 acceptance 专用 PostgreSQL 数据库和 Redis DB，运行
同一条严格门禁。默认 `baseline_path` 指向已提交的
`tests/acceptance_app/baselines/compatibility-result.langgraph-1.2.11.json`，因此 checkout 后
即可执行能力差分；比较另一历史版本时显式传入对应快照。缺少 baseline 会失败而不是放行。
结果和能力级 diff 会作为 workflow artifact 上传。workflow_dispatch 还提供可选的
`cross_network_sse_url`；只有填写独立部署的 GraphHarbor URL 时才执行跨网络 SSE 门禁。
留空时只运行本机协议 fixture，不会把 loopback 结果标记为跨网络通过。

runner 默认是严格门禁：任何 `failed`、`blocked_external_dependency` 或 `not_run`
都会返回非零退出码。`--allow-incomplete` 仅用于明确标注的非门禁诊断运行。

差分工具不会启动服务。它比较 `/ok`、`/info` 和 `/openapi.json` 的路径/方法；workflow 还会运行 [`official-protocol-scenario.json`](../tests/javascript/fixtures/official-protocol-scenario.json)，逐步比对 assistant、thread、Store 生命周期、thread-scoped stream 和最小 `runs/stream` SSE。Store 与 `/threads/{thread_id}/stream` 必须直接通过 OpenAPI 和场景差分，不得加入排除项。可用 `--probe GET:/path` 增加 HTTP 探针，或使用 `--sse-path /path` 比较已创建 run 的 SSE 帧序列。官方新增 endpoint 会导致比较失败；只有在 [`compatibility-exclusions.json`](compatibility-exclusions.json) 明确记录为不支持能力时，才可通过 `--ignore-openapi-path` 或 `--ignore-openapi-method` 排除。排除项是当前 core profile 的边界，不代表 GraphHarbor 已实现该官方能力。

升级完成并人工确认兼容矩阵后，应将本次结果的最小能力快照提交到
`tests/acceptance_app/baselines/`，以供下一次升级使用。例如：

```bash
cp artifacts/compatibility-result.json \
  tests/acceptance_app/baselines/compatibility-result.langgraph-<version>.json
```

`compare_compatibility_results.py` 找不到 baseline 时默认返回非零；只有显式传入
`--allow-missing-baseline` 才允许生成 `informational` 首次运行报告。没有 baseline
不得作为兼容性门禁通过。

如需区分官方版本行为变化与 GraphHarbor 自身回归，可额外提供四个已保存的结果快照，
运行四象限比较：

```bash
uv run python scripts/compare_compatibility_quadrants.py \
  --official-old tests/acceptance_app/baselines/official-old.json \
  --official-new tests/acceptance_app/baselines/official-new.json \
  --graphharbor-old tests/acceptance_app/baselines/graphharbor-old.json \
  --graphharbor-new artifacts/compatibility-result.json \
  --out artifacts/compatibility-quadrants.json
```

它输出旧官方/新官方、旧 GraphHarbor/新 GraphHarbor、旧官方/旧 GraphHarbor、
新官方/新 GraphHarbor 四组 capability-level diff。缺少任一快照默认失败；只有显式
`--allow-missing-snapshot` 才能用于非门禁诊断。workflow_dispatch 中的四个可选 snapshot
输入全部填写时会自动执行该门禁。
