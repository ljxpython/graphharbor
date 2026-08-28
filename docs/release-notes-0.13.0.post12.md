# GraphHarbor 0.13.0.post12

## 修复

- TestPyPI 安装验收先确认两个精确版本已出现在 simple index，再用 `uv pip install` 安装，避免 `uv run --with` 的多索引解析误报发布失败。
