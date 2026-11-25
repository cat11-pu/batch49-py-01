# batch49-py-fix-a-03

## 题面

合规抽检要求拿出最近一次文件完整性漂移的完整证据，运维翻出来的却是三份互相对不上的清单：一份按目录整棵重扫、一份只记了哈希、还有一份把已经隔离的文件又当成普通漂移算了进去。问题落在 gsb/batch49-py-fix-a-03 的 fim_drift_ledger.py，类 FimDriftLedger，用它把基线、观测、漂移与隔离工单串成一条能增量比对、能隔离取证、也能恢复或接纳的账本。文件系统用内存账本模拟，不落真实磁盘。

公开入口：FimDriftLedger(*, max_paths=64, max_open_drift=16)；set_baseline(path, *, digest, size, mode=0o644, owner="root", inode=1)；baseline_for(path)；observe(path, *, digest, size, mode=0o644, owner="root", inode=1, now_ns=0)；compare(*, since=None)；quarantine(path, *, now_ns=0)；tickets()；restore(ticket_id, *, now_ns=0)；accept(ticket_id, *, now_ns=0)；snapshot()；restore_state(state)；stats()。拒绝与失败统一抛 FimDriftError(code, detail)。

要落地的行为：①路径必须是绝对 POSIX 路径，连续斜杠与单点段要归一化，出现空串、相对路径、双点段或 NUL 一律 FIM_INVALID_PATH；digest、owner 非空，size 不小于 0，mode 不超过 0o7777，inode 不小于 0，否则 FIM_INVALID_RECORD。基线路径与观测路径合计受 max_paths 硬顶，超限 FIM_CAPACITY_EXCEEDED 且不落半条记录。②set_baseline 首次写入版本为 1；五个字段与现有基线完全相同视为幂等，返回当前版本且不推版本；任一字段变化则版本加一并整体替换。③observe 记录 digest、size、mode、owner、inode 五项，返回全局观测序号，序号从 1 开始且只在内容实际变化时递增；五项完全相同的重复上报返回原序号，不推进序号也不增加观测计数。④compare 对基线与观测的并集逐路径比对：只有观测没有基线记 added，只有基线没有观测记 removed，两边都有但五项有差异记 modified，changed 为差异字段名按字典序排列的元组，结果按路径升序；完全一致返回空元组。⑤compare(since=None) 传了序号时只回传序号大于 since 的条目，序号为 0 的 removed 条目在该模式下不出现；不传时回传完整漂移集合。⑥quarantine 只接受当前存在漂移的路径，否则 FIM_NO_DRIFT；同一路径重复隔离报 FIM_ALREADY_QUARANTINED，未关闭工单数达到 max_open_drift 报 FIM_DRIFT_CAPACITY；工单号形如 q-1，带路径、漂移类型、基线版本、观测摘要与封存时间，隔离不删漂移证据，compare 仍能看到该路径。⑦隔离中的路径再次上报观测必须失败关闭，不能覆盖隔离证据或清掉漂移。⑧restore 把基线内容写回成新的观测并关闭工单，返回 True 并计入 restored；若当前观测已与基线一致，则无写入、直接关闭工单并返回 False，不推进基线版本；未知工单报 FIM_TICKET_UNKNOWN，已关闭报 FIM_TICKET_CLOSED，只有 added 漂移、没有基线的路径报 FIM_NO_BASELINE。⑨accept 把当前观测提升为新基线，已有基线时版本加一、没有基线时版本从 1 起，关闭工单并返回新版本；没有观测的路径报 FIM_NO_OBSERVATION。⑩tickets 只列未关闭工单并按工单号升序；stats 固定返回 baselines、observed、open_drifts、open_tickets、observations、restored、accepted 七个键。⑪快照深拷贝基线、观测、隔离状态、已关闭工单与计数，恢复非法结构报 FIM_SNAPSHOT_INVALID 且不改动现有状态，恢复后隔离关系与观测序号必须原样可用。

硬约束：set_baseline、observe、compare、quarantine、restore、accept 共用一把可重入锁，compare 只能看到一次完整写入之后的账本，隔离与观测不得交错出“工单已建但观测又被覆盖”的中间态；快照恢复后基线版本、观测序号、开放工单与统计必须完整复原，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: fim_drift_ledger
- typeName: FimDriftLedger
