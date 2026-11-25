# batch49-py-safety-a-07

## 题面

审计值班在核对多来源日志时发现，核心网关的事件序号从 18 直接跳到 23，中间几条再也没出现；补录程序把 19 补回来之后，页面反而把同一条事件显示了两次，缺口列表也一直挂着不消失。三个网关的审计流同时接进来，值班只能按条数猜哪一段丢了，原始日志还留在磁盘上，谁也不确定该以哪份为准。范围是 gsb/batch49-py-safety-a-07 的 audit_sequence_stitcher.py，类型 AuditSequenceStitcher，按来源维护单调序号、缺口区间、补录与消费游标，异常统一抛 AuditSeqError(code, detail)。

公开接口：ingest(source, seq, body, *, ts=0)；backfill(source, seq, body, *, ts=0)；gaps(source, *, now=None)；close_gap(source, start)；cursor(source)；ack(source, seq)；resume(source)；events(source)；generation(source)；retire(source)；sources()；snapshot()；restore(state)；stats()。events 每行含 seq、body、ts，缺口对象含 start、end、missing、discovered_at、status，快照含 version、sources、stats，stats 含 accepted、duplicates、buffered、dropped、backfills、gaps_closed、retirements。

要修的行为：①同来源 seq 严格加一，收到连续序号就推进前沿，ingest 返回 status 与 frontier，来源重置序号从 0 重新开始时按新一代来源处理、generation 加一，不能把 seq 回退当成缺口；②重复 seq 去重返回 duplicate、不追加第二条，同一 seq 两条内容不同时保留第一条并报冲突，不能静默覆盖，也不能把第二条算进缺失；③跳号按区间记录，不逐条展开，区间带起始、结束、缺失条数与首次发现时刻，区间之间不重叠、相邻的缺失要并成一段，补录窗口按首次发现时刻统一时钟判定，跨时区来源不能各按本地时区算窗口；④补录到达后缺口收缩，补齐后缺口消失，恢复出来的视图要和实时收到的一致；⑤补录与实时事件混合时 events 按 seq 排序，不按插入时刻；⑥同一缺口关闭后状态转 unclosable，明细里仍要能看到起止与缺失条数，后续补录抛 AUDIT_SEQ_GAP_CLOSED；⑦close_gap 返回缺失条数，重复关闭返回 0，未命中返回 0，关闭后不再接受补录；⑧消费游标只由 ack 推进，只能前进，回退抛 AUDIT_SEQ_CURSOR_REGRESSION，否则下游账单会被重复算一遍，越过连续前沿抛 AUDIT_SEQ_NOT_DELIVERED；⑨resume 返回游标加一，重订阅从这里开始；⑩来源注销不是删除，注销后缺口仍可查询、游标冻结，历史还能复盘，retire 返回是否首次注销，再 ingest 或 ack 抛 AUDIT_SEQ_SOURCE_RETIRED；⑪缓冲有上限，满了丢最旧未补事件并计 dropped，已经连续的那一段不许丢；⑫快照深拷贝，含 sources 与 stats，恢复后缺口、游标与去重集合一致，已消费事件不重放，重启后从断点继续而不是从头开始。

硬约束：同一来源的事件只能消费一次，游标跨重启只能前进；缺口与缓冲都是有界结构，缓冲上限由 buffer_cap 控制，长期运行不能无限增长，同一个缺口也不许重复计数。验收：python3 -m pytest -q tests 的 15 条全过，不改测试、不跳过、不按固定输入写死；并发 ingest 与重启恢复自行验证；只做本地实现，最后交源码、跑测记录和一页以内的序号与游标说明。

补录前先校验来源游标与缺口区间边界，避免把已消费区间重新打开。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: audit_sequence_stitcher
- typeName: AuditSequenceStitcher
