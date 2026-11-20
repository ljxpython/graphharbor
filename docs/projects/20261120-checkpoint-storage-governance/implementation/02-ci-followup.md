# 远端失败修复与测试资源回收

关联 S07，日期为 2026-11-21。本记录属于阶段验证，不替代正式产物或生产验收。

## 领取与请求数据故障

内部上游 Worker 从 `ops.Runs.next()` 领取 Run 后，没有建立 journal baseline；saver 按 Run ID 写入撤销记录时被外键拒绝。现在在原有 Run/Thread 锁内、改为 running/busy 之前调用同一个 `capture_baseline`，不另建格式或业务分支。

`_get_thread` 曾把 `thread_id` 加进传入的请求字典。线程状态 PATCH 转发到元数据更新时重新读取同一缓存 JSON，严格字段校验将该内部键拒绝为 422。现在只复制授权事件字典，原请求内容保持。

新增真实 PostgreSQL 回归覆盖内部领取后的实际 saver 写入，以及状态 PATCH 的成功、跨项目拒绝和未知字段拒绝。修复前分别复现 baseline 缺失与 422；中途第一次替换误命中另一个同名表达式，读回后撤回误改并修正。最终定向套件 37 passed，2 个上游 Beta warnings。

## 完整门禁和夹具身份

所有破坏性用例均通过 `scripts/run_isolated_tests.py` 使用一次性原生 PostgreSQL/Redis。未连接仓库默认库或使用方现役库。

第一轮完整 SDK 验证出现 50 passed、5 failed。五个上游 RemoteGraph v3 用例把服务地址写死为 localhost:2024；本次所属服务使用随机端口，连接失败不是实际执行通过。新增小型 pytest 插件只调整该模块的测试地址，保留五个用例。独立回归验证不会修改其他模块地址。

测试看门狗原先只终止父 shell，遗留 `sleep 900` 持有输出管道。现在显式回收计时子进程，并为套件建立独立进程组，超时可回收其后代。上游单例限定 60 秒，整套预算维持 900 秒。

带预算完整运行：本仓单元/队列 31 passed（8.82 秒）、live-server E2E 13 passed（7.16 秒）、上游 Python SDK 55 passed（37.92 秒），脚本退出 0。随后确认所属 API、计时器和临时 PG 进程已结束。一次较早运行中断且脚本被运行时修改，产生语法错误；该轮不计通过，后续不修改执行中的脚本。

JavaScript SDK 1.9.28 完整契约通过，使用独立原生库及随机 API 端口，API/Worker 在 finally 中逐个回收。

宽回归首轮为 404 passed、9 skipped、2 failed（138.71 秒）。两项失败均在 Cron 测试的专属数据库名守卫处停止；没有放宽守卫，改用其指定数据库名和 Redis 前缀后独立 Cron 3 passed（6.60 秒）。跳过项不算通过，套件存在重叠不累加样本数。

最终 Ruff 检查与格式检查通过（92 个文件）；mypy 46 个源码通过，仅保留既有未用 deepagents 配置提示。真实 1 秒总预算的超时演练返回 124，未遗留所属 API、pytest、PostgreSQL、Redis 或计时子进程。

## 终态事件去重与重复执行

一次完整通过不能证明没有竞态。追加五轮 RemoteGraph v3 重复测试时，三轮全部通过、两轮各有一个 interrupt/resume 超时；添加诊断的另外五轮全部通过。首次无单例预算的运行也曾出现同步输出等待。当前不能将这些现象归为已修复、仅由端口引起或产品无关。继续用同版本官方服务和最小场景核对，发布前形成结论，不用删除用例或旧格式 fallback 掩盖失败。

## 回收边界

本轮私有日志与临时 runner 位于仓库私有 `.local/checkpoint-followup-20261121/`；正式项目证据保留在本目录。修复交付 main 且必要证据保存后才清理私有夹具。既有其他专项的 artifacts、release-candidates 和发布历史不属于本轮垃圾。

同版本官方 in-memory 对照在固定依赖后连续五轮、每轮五项通过。第一次对照因并行依赖同步移除了 deepagents 而启动失败，不记协议结果；此后测试期间不再同步同一个安装环境。测试脚本和 CI 在初始同步与专用依赖安装后使用 `uv run --no-sync`，避免夹具依赖被后续调用改写。

客户端只在失败时输出的记录再次捕获同步 output 等待，以及恢复后收到子图 completed 却缺少根 completed。进一步加入服务端记录会改变时序，30 轮均通过，因此不能把诊断版通过当原始竞态已消失。

最小确定性测试复现两处错误：控制消息的本地时钟 ID 与数据消息的 Redis ID 属不同 ID 空间，却共用同一个去重键；线程订阅又把不同 Run 的同值 ID 当作同一事件。修复前两个测试分别在数据队列为空、第二个 Run 被拒处失败。现在去重键均包含原生 topic，重复传递同一事件仍被抑制，游标过滤保持。两个回归测试通过；无诊断的上游 RemoteGraph v3 连续 30 轮、每轮五项全部通过（150 次调用），覆盖同步输出、中断、恢复与 abort。此为固定夹具结果，不宣称所有可能竞态均已消除。

修复后完整脚本再次退出 0：本仓单元/队列 32 passed（9.31 秒）、live-server E2E 13 passed（6.78 秒）、上游 Python SDK 55 passed（37.82 秒）；JavaScript SDK 1.9.28 完整契约也再次通过。post50 四产物构建成功，62 个 Python 源码与工作树逐字节一致，未混入私有配置或使用方源码；正式发布、冷安装与远端 CI 尚待后续记录。

post50源码扩大回归为405 passed、9 skipped、13 deselected（155.57秒）；13项live-server E2E已在独立完整脚本实际通过，Cron专属库另验。Python3.13从候选wheel的site-packages执行41 passed、1 deselected（35.73秒），未纳入的内部claim入口需要可选上游langgraph-api，已在源码完整脚本及定向回归实际通过。候选安装不是正式源验真。

本轮Cron首次补验把专属Redis前缀的连字符写成冒号，被守卫原子拒绝（2 failed、1 passed）；改回既定 `graphharbor:cron-probe-20261005` 后三项通过。没有修改隔离守卫或连接非所属库。
