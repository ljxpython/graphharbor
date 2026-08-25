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

差分工具不会启动服务。它比较 `/ok`、`/info` 和 `/openapi.json` 的路径/方法；workflow 还会运行 [`official-protocol-scenario.json`](../tests/javascript/fixtures/official-protocol-scenario.json)，对 assistant、thread 和最小 `runs/stream` SSE 逐步比对。可用 `--probe GET:/path` 增加 HTTP 探针，或使用 `--sse-path /path` 比较已创建 run 的 SSE 帧序列。官方新增 endpoint 会导致比较失败；只有在 [`compatibility-exclusions.json`](compatibility-exclusions.json) 明确记录为不支持能力时，才可通过 `--ignore-openapi-path` 或 `--ignore-openapi-method` 排除。排除项是当前 core profile 的边界，不代表 GraphHarbor 已实现该官方能力。
