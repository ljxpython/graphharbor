# post29 本地候选：生命周期对齐

状态：2026-09-15 已发布PyPI，两包均为0.13.0.post29，远端四产物SHA256与下表一致。实际wheel独立API／Worker验收通过；未宣布完整官方兼容。

- 根线程生命周期running、graph_name、字符串error；子事件目标scope和实际graph_name；原样保留cause。
- 新持久事件timestamp稳定；run.start/input.respond成功响应补type，官方JS SDK可消费。
- 保留既有事务终态、取消／恢复状态以及Run SSE v3扩展；官方0.13.0 Run SSE传v3并不返回同样的typed格式。
- 双端确定性图和JS SDK成功、失败、中断恢复通过；严格生命周期字段对照仍有26项附加字段／子中断差异。官方HITL源Run持久success、GraphHarbor为interrupted另作状态差异记录。
- 最终Server全量159 passed/18 skipped（67.49秒）；参考图另3 passed。源码/scripts lint与格式、36个源码文件mypy、版本与锁检查通过。
- API真实重启后6条完整、4条cursor后、2条子scope回放逐字段一致。

构建文件：两包pyproject、uv.lock、scripts/check_versions.py锁步post29；只变更两包版本，冻结LangGraph/SDK/CLI版本未变。

```bash
uv lock --check
python3 scripts/check_versions.py
uv build --package graphharbor-runtime --out-dir artifacts/v3-dist
uv build --package graphharbor --out-dir artifacts/v3-dist
```

四个产物SHA256：

| 产物 | SHA256 |
|---|---|
| graphharbor-0.13.0.post29-py3-none-any.whl | 47dea16fbf15ef16b349d5c214f991e29aa6dcf7ff47b215737ce818bca50baa |
| graphharbor-0.13.0.post29.tar.gz | c5064fc93190eba97145fc90b76801db43097dbe47a8c452f6de5a1949af0e77 |
| graphharbor_runtime-0.13.0.post29-py3-none-any.whl | 3c527e222020fb3164e899636058c2681a01f5867b166f509b6cd30f3546db17 |
| graphharbor_runtime-0.13.0.post29.tar.gz | a381e0d46d2ade9875861c4fa3e86113fb41ffb73af6061b21e275acf6ae806b |

干净环境`/tmp/graphharbor-v3-wheel-check-20260915`安装两个wheel，CLI显示post29，公共Server/协议模块导入及根running投影通过；没有安装langgraph-api。该环境按包允许范围解析到langchain-core1.6.3，和官方对照环境1.6.0不同，导入检查不等于该依赖组合全链路验证。

后续已完成该干净环境的真实API31396／Worker：HTTP嵌套／多中断／传输过滤／游标矩阵、JS SDK消息／工具／HITL、40次性能、在途v3期间新建v2回退。证据为artifacts/v3-matrix-wheel.json、v3-performance.json、v3-inflight-rollback.json及平台03实施记录。平台也已安装post29并通过文件、研究、双子任务、父取消与Langfuse回读。前端浏览器与客户端默认切换后置。
