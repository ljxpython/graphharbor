# 存储、转换与恢复实证

日期：2026-11-21。关联S02–S06。所有数据库均为独立临时PostgreSQL/Redis，未连接现役库；通用确定性图不依赖平台源码或模型。

## 为什么替换完整基线

旧正式post48对应源码和旧依赖1.2.11/4.2.0/3.1.2，在每Run首次领取时将所有checkpoint/writes和完整values保存为JSONB。真实夹具10000条、每条16KiB时，PostgreSQL拒绝建立边界：`total size of jsonb array elements exceeds 268435455 bytes`。这证明该夹具下的确定故障，不证明现役OOM因果。

新实现claim只读取根head和必要控制字段；子namespace首次触碰时记录head。三个原生表以真实主键记录首个before-image，与saver写入同事务。AFTER触发器避免同version blob的`ON CONFLICT DO NOTHING`被误记为新增。结果投影保存标准JSON Patch反向差量，回滚不加载用户graph/reducer。

## 固定容量结果

夹具单namespace、历史100/1000/10000、负载1KiB/16KiB。旧版每组3次，最后一组失败；新版每组20次，nearest-rank p95。两代样本数不同，不将旧版3次的统计伪装为新版等规模p95。独立维护进程统计RSS；同时存在其他隔离测试和闲置平台栈，不称完全无负载实验室。

| 历史/负载 | 旧边界逻辑字节 | 旧p50 ms | 新p95 ms | 新prune ms | 新峰值RSS字节 |
|---|---:|---:|---:|---:|---:|
|100/1KiB|254193|19.60|20.214|52.681|97660928|
|100/16KiB|3326193|157.31|19.335|51.542|97869824|
|1000/1KiB|2541094|158.87|16.710|218.905|98406400|
|1000/16KiB|33261094|1540.55|13.158|113.798|97964032|
|10000/1KiB|25428095|1439.46|13.143|650.184|99553280|
|10000/16KiB|JSONB超限失败|未完成|12.154|708.201|99983360|

新版六组边界最大330字节、单次边界WAL最大1144字节，均低于用户批准的8KiB/64KiB；p95低于100ms、RSS低于256MiB，10000条prune低于5秒。边界及WAL未随历史负载增长；prune总工作仍随需检查/删除的历史增长，预算从未承诺数据库扫描为常数时间。

原始输入、20次延迟、逐次WAL、逻辑表字节/行数和失败见[capacity目录](capacity/)。测量入口为根目录`scripts/measure_checkpoint_storage.py`，通过`scripts/run_isolated_tests.py`创建临时资源。锁等待夹具为无竞争；并发安全由显式屏障测试覆盖，不从此表外推高竞争p95。DELETE只证明逻辑回收，不保证物理文件立即缩小。

## 恢复与并发

真实PG专项覆盖A→B→回滚B→C、逆序批量回滚、同键覆盖、root Run、主子namespace、Delta跨快照、update_state/fork、缺counter头、pending writes/HITL、过期generation/晚写拒绝、回滚意图跨进程恢复及写/撤销/GC故障原子性。prune使用临时保留集合、递归祖先查询和500行批量删除；全批次同事务持Thread锁，读者不会看到中间破损状态。

migration015仅作一次性转换：先排空并停止所属API/Worker，备份数据库，再运行匹配包迁移。转换在PG内比较旧基线与下一边界/当前行，生成实际变化的undo keys；投影逐边界转换。无法证明checkpoint归属时报告Run并回滚整个迁移。原blob缺少可靠历史归属，转换保留，之后由引用GC回收。

两个迁移参数场景实际执行：旧数据→转换→逆序恢复；注入归属失败→仍为014和旧数组→修复→成功转换；`pg_dump`→删除所属测试schema→`pg_restore`→再转换→回滚逐项一致。downgrade明确拒绝，恢复依据是切换前备份。关闭自动迁移时Schema不匹配也拒绝启动，旧writer缺journal-v1被原子拒绝。

## 测试与限制

源码扩大回归399 passed/22 skipped，独立Cron3 passed；增加严格启动检查后关键矩阵24 passed。独立wheel关键矩阵54 passed/1 deselected，sdist在Python3.13实际安装后26 passed。数量重叠，不相加充当唯一样本。

独立wheel最初35 passed/1 failed：失败用例直接调用内部上游兼容ops，需要可选langgraph-api；生产包刻意不依赖它。该用例保留在有比较依赖的源码回归，独立产物改验公共网关与原生持久化入口，没有给生产加兼容依赖或伪造通过。

官方0.13.0实际双服务严格对照未通过：201项OpenAPI描述差异与post48逐项相同，retention扩展和thread stream额外checkpoint/debug也在旧包复现。更快claim暴露SSE首帧attempt错误加一：新增用例先复现1失败/1通过，统一为已有路径的`max(retry_count,1)`后11项stream/SDK测试通过。完整官方等价性不作为本项目完成声明，也未扩大或静默修改协议排除项。

用户已明确不要求旧代码逐字段兼容。本项目验收通用数据完整性、安全屏障、单格式恢复和批准的维护成本；不宣称任意Agent状态、反向差量、永久原文或固定K周期快照绝对有界。
