# GraphHarbor 0.13.0.post11

## 修复

- TestPyPI 发布后的安装验证以 TestPyPI 作为解析主索引，PyPI 仅作为第三方依赖补充索引，确保验证安装刚上传的两个同版本制品。

## 验证

- PostgreSQL 同 thread 多 worker 领取回归已覆盖 16 路竞争；完整 CI 通过后，先完成 TestPyPI 精确版本安装验证，再发布 PyPI。
