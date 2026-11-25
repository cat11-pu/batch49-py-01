# batch49-py-game-c-10

## 题面

VR 影院的双目画面偶尔左右眼错配，观众看到重影，拆开日志发现有的一对只到了一只眼就先送显，另一只直到下一对才被用掉，重影只在快速转头时出现，静止画面完全正常。处理范围是 gsb/batch49-py-game-c-10 的 stereo_present_barrier.py，类 StereoPresentBarrier 负责一对视图的开启、两视图到齐后的栅栏对齐、超时整对丢弃与统计。两视图各自渲染完成的时间可能相差好几毫秒，栅栏只认视图 ID，不认到达顺序。

公开接口：StereoPresentBarrier(max_inflight_pairs=4, wait_limit_ms=12, max_dropped=1024)；begin_pair(pair_id, at_ms) -> None；submit_view(pair_id, view_id, data, at_ms) -> bool；wait_barrier(pair_id, now_ms) -> str；present_pair(pair_id) -> tuple[bytes, bytes]；drop_pair(pair_id, reason="timeout") -> int；timeout(now_ms) -> list[str]；inflight() -> list[str]；dropped_ids() -> list[str]；stats() -> dict；snapshot() -> dict；restore(state) -> None。view_id 只接受 left 与 right；错误抛 StereoBarrierError，code 用 PAIR_INVALID、PAIR_EXISTS、PAIR_UNKNOWN、PAIR_LIMIT、PAIR_INCOMPLETE、PAIR_TAKEN、VIEW_INVALID、TIME_INVALID、SNAPSHOT_INVALID。

故障与边界：①同一 pair_id 只能开启一次，重复开启报 PAIR_EXISTS；②在飞对数封顶，超限报 PAIR_LIMIT，pair 呈现或丢弃后名额立即释放，名额按对计算；③对未开启的 pair 提交视图报 PAIR_UNKNOWN，视图名或数据非法报 VIEW_INVALID；④只到一只眼 submit_view 返回 False，两视图都到返回 True，返回值为该对是否已经可以提交；⑤两视图不齐时 present_pair 报 PAIR_INCOMPLETE，不呈现半对；⑥present_pair 按视图 ID 返回左右两份数据，一对只能呈现一次，再次呈现报 PAIR_TAKEN；⑦wait_barrier 在等待上限内返回 waiting，两视图到齐返回 ready，超时返回 timedout，超时判断只看到达时刻与当前时刻之差；⑧timeout 扫在飞对，只丢弃超过等待上限的，返回排序后的 pair_id 列表，并把丢弃的视图数计入统计，丢弃记录按上限保留最旧的先淘汰；⑨drop_pair 返回本次丢掉的视图数，0 到 2，已结束的 pair 再丢报 PAIR_TAKEN；⑩同一对里一个视图重复提交按最后一次覆盖，不新增一对也不提前放行；⑪超时丢帧后迟到的单视图必须丢弃，不得留给下一对复用，避免左右眼错配；⑫等待上限内两视图都到但顺序颠倒时按视图 ID 对齐，不按到达顺序配对。

硬约束：提交与超时并发时一对帧只能有一个结局，要么被呈现要么被丢弃，不得两边都记账；在飞对、等待上限与丢弃统计各自封顶；超时扫描与呈现提交共用同一把锁；等待与超时都不阻塞渲染线程，队列长度封顶后新对直接拒绝，所有时间参数都用毫秒整数；快照带走在飞对、丢弃历史与计数，restore 校验失败抛 SNAPSHOT_INVALID 且现有状态不变，恢复后 inflight 与 stats 与快照时一致。stats 至少给出 pairs、presented、dropped_pairs、dropped_views、timeouts 五个计数。

验收：python3 -m pytest -q tests 15 条全过，不改测试、不跳过、不按样例写死；并发提交与超时、重启恢复自行验证；左右眼错配属于事故级问题，边界分支要单独构造用例；统计口径以对为单位、以视图为最小计数；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: stereo_present_barrier
- typeName: StereoPresentBarrier
