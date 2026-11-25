# batch49-py-game-c-09

## 题面

射击游戏的插帧模块上线后，慢镜头里出现重影，快速转身时又像被抽帧，显示端偶尔卡在半帧，同一段录屏在 60Hz 显示器上正常，切到 120Hz 就出现一帧重复。处理范围是 gsb/batch49-py-game-c-09 的 frame_generation_scheduler.py，类 FrameGenerationScheduler 在真实帧之间安排生成帧的呈现槽位，负责插几帧、什么时刻呈现、迟到怎么丢。真实帧与生成帧共用同一套槽位编号，槽位一旦分配就保留到该帧呈现或丢弃。

公开接口：FrameGenerationScheduler(target_hz=50, max_generated_per_gap=2, latency_budget_ms=50, max_inflight_generated=8)；submit_real(frame_id, pts_ms) -> int；plan_slots(frame_id) -> list[int]；present(slot) -> str；drop(slot) -> bool；latency_budget() -> int；refresh_rate() -> int；set_refresh_rate(hz) -> None；stats() -> dict；snapshot() -> dict；restore(state) -> None。槽位号按目标刷新周期切分，pts 毫秒除以周期取整得到槽位；错误抛 FrameGenError，code 用 INVALID_FRAME、FRAME_DUPLICATE、FRAME_UNKNOWN、SLOT_EMPTY、SLOT_ALREADY_PRESENTED、SLOT_NOT_DROPPABLE、INVALID_RATE、SNAPSHOT_INVALID。

故障与边界：①第一帧不插帧，submit_real 返回 0，plan_slots 给空表；②两个真实帧的槽位差减一就是可插帧数，最多 max_generated_per_gap 且无论配置多大都只到 2，生成帧槽位从上一个真实帧槽位加一开始连续排列，不得越过下一个真实帧，槽位号不得为负；③同一 frame_id 重复提交报错，pts 为负或 id 为空报错；④present 按槽位取帧，真实帧返回原 frame_id，生成帧返回 gen:真实帧id:序号，生成帧序号从 1 开始；⑤空槽位呈现报 SLOT_EMPTY，已呈现的槽位再呈现报 SLOT_ALREADY_PRESENTED；⑥drop 只能丢生成帧，真实帧槽位报 SLOT_NOT_DROPPABLE，丢过的槽位再呈现按空槽处理；⑦延迟预算换算成槽位数，预算内排不下的生成帧不排，预算按毫秒配置、换算时向下取整；⑧在飞生成帧数封顶，排不下时计入 skipped 而不是覆盖已排队帧，已排队帧不被挤掉；⑨刷新率非正数报 INVALID_RATE，且失败不得改掉原刷新率；⑩两个真实帧间隔过短时不插帧，槽位留空，不得复制前一帧顶上；⑪生成帧已入队后真实帧更新到达，允许取消未呈现的生成帧并回收槽位，已经呈现的不再回退；⑫刷新率切换后槽位相位按新周期重算，已排队的帧不得跨周期错位。

硬约束：呈现队列加锁，同一槽位只能被一个帧占用，并发 present 同一槽位只有一个成功；统计与在飞计数并发推进不能丢；槽位分配与呈现记账必须在同一临界区完成；刷新率变更只在两次提交之间生效，统计里的 skipped 只增不减；快照带走真实帧、槽位分配、计数与配置，restore 校验失败抛 SNAPSHOT_INVALID 且现有状态不变，恢复后 plan_slots 与 stats 与快照时一致。stats 至少给出 real、planned、presented、dropped、skipped 五个计数。

验收：python3 -m pytest -q tests 15 条全过，不改测试、不跳过、不按固定数字写死；并发呈现与重启恢复要自己构造场景，不能只跑主流程；生成帧的统计口径以槽位为准；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: frame_generation_scheduler
- typeName: FrameGenerationScheduler
