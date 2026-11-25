# batch49-py-safety-a-06

## 题面

夜间批量压测时运维在平台上点了整组冻结，值班同事发现一半容器还在跑，冻结成功的那批在统计面板上 CPU 用量凭空多算了一截；再点解冻，有两组一直卡在中间态，控制面重启之后状态还是对不上，白天的压测报表也跟着失真。问题落在 gsb/batch49-py-safety-a-06 的 cgroup_freeze_coordinator.py，类型是 CgroupFreezeCoordinator，负责一批 cgroup 的冻结、解冻、用量快照与意图日志，所有异常统一抛 FreezeError(code, detail)，detail 里带上出错的路径。

公开接口：add_group(path, *, parent=None, procs=0, quota_cpu=100, quota_mem=1024, now=0)；set_processes(path, *, running, exiting=0, zombies=0, forking=0)；freeze(path, *, now)；thaw(path, *, now, finalize=True)；status(path)；batch_freeze(paths, *, now)；batch_thaw(paths, *, now)；resize(path, *, quota_cpu=None, quota_mem=None, now=0)；usage(path)；rate(path, *, now)；sample_usage(path, *, used_ms, now)；forget(path)；journal()；recover(entries)；stats()。add_group 与 status 返回 GroupView(path, state, frozen_ms, procs, quota_cpu, quota_mem)，usage 返回 used_ms、frozen_ms 两个键，batch_freeze 成功返回 frozen 与 rolled_back 两个键，stats 含 freeze_events、thaw_events、zombie_warnings、fork_retries、rollbacks、recoveries。对外状态只有 thawed、freezing、frozen、thawing 四种。

要修的行为：①freeze 必须从 thawed 经 freezing 到 frozen，thaw 传 finalize=False 时停在 thawing，此时再来 freeze 抛 FREEZE_BUSY；②已经 frozen 的组重复 freeze 要幂等，不重置冻结起点，也不重复累加 freeze_events；③组内还有进程正在退出时冻结必须被拒，抛 FREEZE_PROCS_EXITING，状态停在 thawed，配额与用量都不得改动；④僵尸进程只累加 zombie_warnings，不阻断冻结，也不影响状态迁移；⑤正在 fork 的进程按 retry_limit 次重试，每次计入 fork_retries，耗尽抛 FREEZE_FORK_RETRY_EXHAUSTED，状态同样停在 thawed；⑥frozen、freezing、thawing 期间调用 resize 抛 FREEZE_RESIZE_FORBIDDEN，配额保持原值，解冻之后才允许改；⑦冻结时长只进 frozen_ms，thawing 那一段照算，used_ms 只随 sample_usage 单调累加，出现回落按非法输入处理，冻结期采样抛 FREEZE_SAMPLE_REJECTED，解冻前一直沿用冻结那一刻的快照；rate 的统计窗口要扣掉冻结段，解冻后与新采样衔接，不能出现一段重复计入的时长；⑧批量冻结整批原子，第 n 个失败抛 FREEZE_BATCH_FAILED，detail 里 failed 是失败路径、rolled_back 是按顺序回滚的路径列表，原本已经 frozen 的保持原状，被回滚的组 frozen_ms 归零；⑨batch_thaw 遇到非 frozen 的组整批失败，其余组不单独成功，已经解冻的组按原状重新冻结，回滚不重复计 freeze_events 与 thaw_events；⑩批量条数超过 max_batch、单组进程数超过 max_procs 分别抛 FREEZE_BATCH_TOO_LARGE、FREEZE_PROC_LIMIT_EXCEEDED，且不改动任何组的状态；⑪每一步先落意图日志，日志必须先于状态变更写下，journal 每条是 path、phase、ts 三个键，phase 取四种状态，崩溃后只能看到已经落盘的意图；recover 把 freezing 与 thawing 一律收敛到 thawed，返回 converged 与 states；⑫嵌套组里父组冻结不改变子组状态，解冻父组不得强制解冻已经冻结的子组，组在冻结期间被删除时 thaw 不得把它重新建出来，forget 未知路径返回 False，重复删除也不报错，未知路径的查询与操作抛 FREEZE_UNKNOWN_GROUP。

硬约束：同一组的并发 freeze 只能有一个成功，批量执行中 status 要返回各组各自当时的中间态，不得把整批报成同一个状态，冻结与解冻期间配额不得变化，批量条数与单组进程数都是硬顶，状态与计数器重启后必须一致，不能靠内存里重新猜。验收：python3 -m pytest -q tests 的 15 条必须全过，不许改测试、不许跳过、不许按固定输入写死；并发与重启收敛自行验证；只做本地实现，最后交源码、跑测记录和一页以内的修复说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: cgroup_freeze_coordinator
- typeName: CgroupFreezeCoordinator
