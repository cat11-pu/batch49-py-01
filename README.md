# batch49-py-safety-b-03

## 平台字段（非题面）

| 字段 | 值 |
| --- | --- |
| name | batch49-py-safety-b-03 |
| domain | 安全 |
| sub | 漏洞检测与攻防 |
| type | Bugfix |
| topic | 扫描任务按目标清单分片与断点续扫：分片完成台账、重启续跑与重复分片抑制 |
| module | scan_shard_resumer |
| typeName | ScanShardResumer |
| file | scan_shard_resumer.py |

## 题面

一次覆盖两百万目标的外部资产扫描，执行器被内存压力杀掉两个。重启后现场很难看：有的分片被两个实例各扫了一遍，把对方的 WAF 打成封禁；有的分片一条都没跑却被记成完成，第二天复扫才发现漏了整片网段；还有一次运维在两批之间往目标清单追加了新条目，分片编号整体往后挪，台账上的编号和日志里的片号全对不上。事后统计，那次事故有十七个分片的状态和现场对不上，运维只能全量重扫，多花了六个小时。

这部分落在 gsb/batch49-py-safety-b-03，主文件 scan_shard_resumer.py，核心类型 ScanShardResumer。分片编号从 1 开始且一经分配不再重排；台账是本地的 JSON 文件，包含格式版本、代数、每个分片的目标、状态、认领者与已处理条数。目标条目是主机名或地址，切片顺序按清单原顺序，不排序也不去重后再排。

保留的公开接口（签名不变）：
ScanShardResumer(config=None)
plan(targets) / shard_of_target(target)
mark_started(index) / mark_completed(index, processed) / mark_failed(index, reason)
claim(index, owner) / release(index, owner)
pending_shards() / completed_shards() / failed_shards()
refine(index) / append_targets(targets) / resume(targets) / cancel()
save(path) / load(path) / snapshot() / restore(state)

行为要求：
1. plan 按 max_shard_size 顺序切片，片号从 1 起连续；空清单返回空元组。
2. 片数超过 max_shards 时抛 ShardLimitExceeded，不允许悄悄放大片长。
3. 目标清单有重复条目时抛 DuplicateTargetError，整单不落地。
4. shard_of_target 返回所属片号，未知目标返回 -1。
5. 状态只会 pending → running → completed / failed；mark_started 命中非法状态返回 False，不改台账。
6. 分片执行到一半被打断时只能留在 running，不能因为已处理条数够多就记成完成。
7. resume 只补跑未完成分片，已完成片号按升序回给调用方；失败片重新进入待跑集合；续跑报告要带上代数，调用方据此判断台账是不是本次运行写下的。
8. 目标清单两次运行之间有新增时，只为新增目标追加新分片，已有片号不变。
9. refine 把一个失败分片对半拆成两个新分片，片号从当前最大片号之后继续，原失败记录保留；片长已到 min_shard_size 或片数已到上限时不拆，原样返回。
10. 同一分片被两个执行器认领时只有一个拿到 granted，另一个在 ClaimResult 里看到持有者；认领信息要落进台账，释放后才能被别的执行器接手。
11. cancel 保留已完成进度，只清掉待跑集合。
12. save 必须原子：先写同目录临时文件再改名，成功后目录里只留台账本身；load 遇到版本不符或内容损坏抛 LedgerCorruptError。

硬约束：台账写入具备原子性，进程在任意时刻被杀都不能留下半个 JSON；同一实例的认领与状态迁移要互斥，两个线程不能同时把同一片改成 completed；恢复后的代数必须大于等于落盘时的代数；单片目标数不得超过 max_shard_size，总片数不得超过 max_shards，两个上限同时生效。验收：python3 -m pytest -q tests 现有 15 条必须全部通过；不得改测试、不得跳过、不得按固定输入写死；重启与并发场景自行验证；只做本地实现，不引第三方库。

## 运行方式

```bash
cd batch49-py-safety-b-03
python3 -m pytest -q tests
```

测试 15 条，全部使用标准库与本地文件，不依赖第三方包；并发与重启场景由实现自行保证。
