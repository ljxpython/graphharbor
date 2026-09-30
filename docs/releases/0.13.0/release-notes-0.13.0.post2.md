# GraphHarbor 0.13.0.post2

## 修复

- TestPyPI 隔离安装验证采用 `unsafe-best-match` 索引策略，使 GraphHarbor 包从 TestPyPI 安装、第三方依赖从 PyPI 解析，避免 TestPyPI 的过旧镜像阻断依赖求解。

## 版本

| 项目 | 版本 |
|---|---|
| GraphHarbor / runtime | 0.13.0.post2 |
| Python | 3.11 / 3.12 / 3.13 |
| LangGraph | 1.2.11 |
| langgraph-sdk | 0.4.3 |
| langgraph-cli | 0.4.31 |
