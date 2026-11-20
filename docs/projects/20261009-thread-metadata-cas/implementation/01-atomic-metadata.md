# 通用metadata原子更新

2026-10-09；C01/C02。用户批准使用方F07 R3源码配套，不发布/提交、不迁移数据库、不覆盖其他专项工作树修改。

- `thread_metadata.py::patch_thread_metadata()`：通用JSONB原子merge/条件比较，filters与条件同SQL，null/missing匹配、409/不可见404/422边界。
- `ops.py::Threads.patch()`：复用共享存储helper，签名保留metadata并可选if_metadata。
- `core_api.py::threads_update()`：普通PATCH原子merge与缺省/null/ttl-only/minimal兼容；新增 `threads_metadata_cas()` 严格要求条件。
- `server.py`：独立 `PATCH /threads/{thread_id}/metadata/cas` route/OpenAPI，旧服务路径不存在明确拒绝，不静默忽略条件。
- `tests/test_thread_metadata_cas.py`：真实临时PG，多session屏障、两种rename顺序、无关键merge/null/filter与HTTP契约。

4项PG/HTTP通过；v2四产物cold install与使用方8场景/真实受管模型通过。官方post43无CAS，需唯一新双包版本，C03正式接入blocked；所有证据在verification.md。
