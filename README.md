# batch49-py-game-c-06

## 题面

长片连播到第三个小时，字幕比画面快了将近半句，换一集之后偏移量又变了，对轴师每次都要从头再捋一遍，上线三天，同一个片源在两个机房算出的偏移量还对不上。处理范围是 gsb/batch49-py-game-c-06 的 subtitle_drift_corrector.py，由类 SubtitleDriftCorrector 把人工锚点拟合成可复用的时间映射，再批量改正字幕条目。锚点由对轴师在审片台逐段打点，采集侧会同时送来多路记录，因此同一时刻可能被重复写入。

公开接口：SubtitleDriftCorrector(max_anchors=64, max_segments=8, max_shift_ms=4000, anchor_ttl_ms=600000)；add_anchor(media_ms, drift_ms, added_at_ms=None) -> bool；anchor_count() -> int；anchors_for(now_ms=None) -> list；fit_segments(now_ms=None) -> list[tuple[int,int,float]]；map_time(media_ms, now_ms=None) -> int；apply(cues, now_ms=None) -> list[tuple[int,int,str]]；max_shift() -> int；stats() -> dict；snapshot() -> dict；restore(state) -> None。锚点表示某条字幕时刻的实测漂移，正数代表字幕要往后推；非法入参与超限一律抛 SubtitleDriftError，code 用 ANCHOR_INVALID、ANCHOR_LIMIT、CUE_INVALID、SNAPSHOT_INVALID。

故障与边界：①锚点按 media_ms 升序保存，同一时刻同一漂移重复加入返回 False 不新增，新锚点返回 True；②两个锚点之间按线性插值取漂移，map_time 返回字幕时间加漂移并四舍五入到整数毫秒，首个锚点之前与末个锚点之后按最近锚点的漂移整体平移；③单次校正绝对值不得超过 max_shift_ms，截断后的实际值计入 max_shift()；④fit_segments 每两个相邻有效锚点成一段，斜率等于漂移差除以时间差，段数超过 max_segments 时把斜率最接近的相邻两段合并，合并段用原始端点漂移的弦斜率；⑤超过 anchor_ttl_ms 未再更新的锚点不参与拟合，分段里也不得出现；⑥apply 返回的条目要保住原时长与文本，并按开始时间排序、不得出现后一条开始早于前一条；⑦同一份字幕连跑两次 apply 结果逐项相同，不得叠加漂移；⑧结束早于开始或时间不是整数的 cue 整批拒绝，抛 CUE_INVALID，锚点与统计不动；⑨锚点数量到顶后新锚点抛 ANCHOR_LIMIT，已有锚点不被顶掉；⑩只剩一个锚点时退化为整体平移，映射斜率固定为 1，不允许用单点硬拟合斜率；⑪校正后 cue 开始时间小于 0 时把整条字幕整体后移，不裁掉该 cue，后移量计入最大校正量；⑫同一时刻先后加入两个不同漂移的锚点时保留后加入者并记一次冲突，已经算好的分段不回头重写。

硬约束：多路采集线程同时加锚点不得丢锚点、不得读到半套状态；snapshot 返回的锚点、上限与统计必须是深拷贝，restore 校验失败抛 SNAPSHOT_INVALID 且现有状态不变，恢复后 map_time 与统计要和快照时刻一致。统计与快照都按毫秒整数记账，浮点只出现在分段斜率里，避免回放时出现半毫秒抖动。stats 至少给出 anchors、segments、conflicts、max_shift_ms、cues 五个计数。

验收：python3 -m pytest -q tests 15 条全过，不改测试、不跳过、不按固定输入写死；并发加锚点与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: subtitle_drift_corrector
- typeName: SubtitleDriftCorrector
