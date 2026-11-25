# batch49-py-game-c-02

## 题面

彩排第二遍，调音师按场景召回，推子一段一段爬，人声忽大忽小；静音该在半程一次合上，却抖了七八次，现场扩声里听见一串咔哒；他中途手动压下一路推子，过渡末尾又被拉回快照值，主持人的垫乐忽然顶上来。事后翻操作日志才发现：召回期间的手动改动被插值盖掉，排除项只在开头生效一次，连续两次召回还会从上一次的终点突然起跳，本该平滑的一次换场被拆成一串补救动作。

范围是 gsb/batch49-py-game-c-02 的 mixer_snapshot_recaller.py，类 MixerSnapshotRecaller，把推子、声像、发送量、静音、旁路存成命名快照，召回时按过渡时长逐参数插值，并允许把某些参数排除在召回之外。它只动这五个参数，不碰音频数据本身，座舱屏与调音台读的是同一份现值。公开接口：MixerSnapshotRecaller(max_snapshots, max_params, max_transition_ms)、set_param(name, value)、capture(name)、recall(name, transition_ms)、set_exclude(names)、compare(first, second)、undo_recall()、interpolate(ms)、snapshot()、restore(state)、transition_state()、stats()；拒绝抛 RecallError(code, detail)，错误码与 stats 键取模块顶部 ERROR_CODES、STATS_KEYS。

边界：
①默认 fader 0.8、pan 0、send 0、mute 假、bypass 假，顺序固定，current_values 按这个顺序给；
②未知名、布尔当数值、数值越界、开关收非布尔都拒，被拒的调用不改变任何现值；
③capture 存副本，之后改现场值不影响快照；同名再捕一次算新版本并递增 revision；
④快照超 max_snapshots 或空名字拒，拒掉的那次不占名额；
⑤transition_ms 为 0 立刻到位且过渡非活动，超限或负数拒，快照缺失拒；
⑥数值参数按 elapsed/duration 线性插值，走满时精确等于目标，再插值不越界；
⑦过渡中手动改某参数，以手动值为准并退出本轮插值，其余参数照旧推进；
⑧排除项始终跟现场值走，召回开始时刚被手动改过也一样保持手动值，不参与插值；
⑨静音与旁路是离散量，只在过渡中点翻一次，不做逐点插值，避免继电器抖动；
⑩连着召回两个快照时从当前中间值出发，不等上一轮走完也不跳到上一轮目标；
⑪compare 按名排序返回差异项，undo_recall 回到召回前并取消过渡，没得回退就报错，transition_state 报活动与进度。

硬约束：过渡推进与手动改动可能并发，锁内更新，参数不得半写或丢值，一次插值读到的五个参数必须属于同一时刻；召回期间改排除项或新增快照，不得改写本轮已算好的目标；snapshot 要脱离活对象，restore 后参数现值、快照集合、排除项与计数都在，非法快照整份拒且不动现场，所有上限按调用前的状态判定。

验收：`python3 -m pytest -q tests` 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: mixer_snapshot_recaller
- typeName: MixerSnapshotRecaller
