# batch49-py-safety-b-16

## 题面

崩溃语料隔离区这周积压了两百多条，提升记录和复现记录对不上：有的语料只跑了一次就被送进主语料并自动开了问题单，第二天回归一次也复现不出来，研发按单子查了半天是误报；同一崩溃指纹被拆成十几条各自计数，去重报表永远偏低，按严重度排序也看不出谁最该先看；隔离区没有上限，一次批量导入后进程内存翻了三倍，只能停服重启，重启后提升台账全部归零，客服找不到哪条语料已经开过单。范围是 gsb/batch49-py-safety-b-16 的 fuzz_corpus_quarantine.py，类型 FuzzCorpusQuarantine，负责崩溃语料的隔离、复现一致性判定、提升与淘汰。

公开接口（签名不变）：FuzzCorpusQuarantine(*, capacity=8, required_reproductions=3)；submit(corpus_id, fingerprint, *, severity, payload_sha, at) 返回 QuarantineEntry；record_reproduction(corpus_id, *, reproduced, at)；mark_unstable(corpus_id, *, at)；review(corpus_id, *, at) 返回 "eligible" 或 "ineligible"；promote(corpus_id, *, at) 返回 Promotion；entry(corpus_id)；entries(*, state=None)；fingerprint_groups()；prune(*, at)；evicted()；promotions()；snapshot()；restore(state)；stats()。拒绝时抛 QuarantineError(code, detail)。

要修的点：①提交必须校验 corpus_id 与 fingerprint 去掉首尾空白后非空、severity 落在 1 到 5，任何一项不合法都报 QUARANTINE_INVALID 且不落库，避免脏语料占额度；②同一 corpus_id 重复提交报 QUARANTINE_DUPLICATE，已经被淘汰的 id 允许重新提交，重新提交要沿用首次提交时间，计数器清零；③entries 统一按 severity 降序、提交时间升序、corpus_id 升序排列，state 过滤只影响可见集合，不改变排序；④record_reproduction 每次累加 reproductions，reproduced 为真时 streak 加一并刷新最近复现时间，为假时 streak 归零、状态置为 unstable，同一语料在 promoted 或 evicted 状态下拒绝记录；⑤mark_unstable 可以重复调用不报错，但统计里的 unstable 只在状态首次变化时加一，重复标记不能把计数刷高；⑥复现次数没到 required_reproductions 的语料不得提升，达到之后 promote 生成前缀为 ISSUE- 的问题单号、写入提升台账，并把语料移出隔离区；⑦提升之后再调 record_reproduction 或 promote 一律报 QUARANTINE_STATE，未知 corpus_id 报 QUARANTINE_NOT_FOUND，错误不能把已提升的条目改回隔离中；⑧prune 按 capacity 收紧隔离区，先淘汰最近复现时间最早的一条，时间相同淘汰 severity 低的，再相同按 corpus_id 升序，一次只淘汰到容量为止；⑨被淘汰条目保留完整元数据供查询，evicted() 按淘汰先后返回，对淘汰条目提升或记录复现报 QUARANTINE_EVICTED，元数据不得被后续操作覆盖；⑩标记为 unstable 的语料在后续复现重新达标、review 返回 eligible 后能回到隔离区并再次参与提升，review 对未达标语料返回 ineligible 且不改状态；⑪fingerprint_groups 按崩溃指纹归并，同一指纹下的语料各自保留独立条目并合并计数，不得为了去重把条目合成一条，也不得因为归并丢掉某个 corpus_id；⑫snapshot 返回深拷贝，调用方修改快照不能影响实例，restore 校验结构与字段类型，坏快照报 QUARANTINE_SNAPSHOT_INVALID 且现有状态一条不动。

硬约束：record_reproduction、promote、prune 会被多个扫描线程并发调用，同一语料只能提升一次、计数一条不能丢，并发下的 streak 与状态迁移要串行；隔离区额度只统计 quarantined 与 unstable 条目，已提升的立刻让出额度，容量判断不得把淘汰条目算进去；stats 固定返回 submitted、reproductions、unstable、evicted、promoted 五个计数，每个操作只许加一次，重复调用与失败调用都不能计数。

验收：python3 -m pytest -q tests 15 条必须全过，不改测试、不跳过、不按固定输入写死；并发提升与快照恢复自行验证；只做本地实现，交付源码与运行记录。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: fuzz_corpus_quarantine
- typeName: FuzzCorpusQuarantine
