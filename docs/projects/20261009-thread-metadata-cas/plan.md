# Thread metadata 原子更新方案

2026-10-09 使用方F07 R3已获用户批准；本轮实现通用引擎源码与候选验证，不发布、不提交Git、不迁移Schema。

`libs/langgraph-runtime-pg/src/langgraph_runtime_pg/thread_metadata.py::patch_thread_metadata()` 持有共享存储语义：`metadata || patch`，可选非空顶层条件（最多32键、键长256），授权filters与所有条件同一条UPDATE RETURNING。null匹配missing/null；无匹配行再查相同可见filters，存在为409，不可见为404。比较不使用进程锁或先GET再PATCH。

`ops.py::Threads.patch()` 与 `libs/langhost/src/langhost/core_api.py::threads_update()` 共用helper。普通PATCH保持现有签名/缺省metadata/null/ttl-only/minimal响应；公开新增 `threads_metadata_cas()` 与 `server.py` 路由/OpenAPI，不向核心加入标题或平台概念。

条件调用必须使用独立URI，避免旧引擎静默忽略新字段。API/Worker双包匹配，旧引擎返回404/405不能作为普通写成功。JSONB顶层浅合并不执行deep merge；未提交键保留，提交null写null，不代表删除键。

候选仅安装在临时venv。源码版本post43不证明PyPI同版本能力；正式发布需要新的唯一双包版本、四产物来源/哈希与正式源cold install。取消等其他专项工作树变更保留，不在本专项宣称已验全部改动。发布/使用方正式接入列C03 block。
