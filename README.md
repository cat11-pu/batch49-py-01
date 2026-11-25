# batch49-py-game-c-14

## 题面

赛事录制中途按了暂停，回来继续录，导出的片段清单却把休息那几分钟算进了媒体时长，回放时字幕整体错位；另一次进程崩溃后重启，清单里最后一段还标着录制中，续录直接覆盖了已经落盘的两分钟画面。还有连续暂停再恢复产生的几段一两帧素材，剪辑师在时间线上一堆碎片里找不到正片。范围落在 gsb/batch49-py-game-c-14 的 recording_pause_controller.py，类型 RecordingPauseController，负责录制的开始、暂停、恢复、停止、片段清单、重启续录与快照。

公开接口：RecordingPauseController(*, fps=30, max_pauses=8, max_segment_frames=300, max_segments=16, min_segment_frames=15, transition_frames=0, bytes_per_frame=4)；start()；pause()；resume(*, transition_frames=None)；stop()；write_frame(wall_ms)；segments()；recover(manifest)；media_time()；state()；status()；snapshot()、restore(state)、stats()。status 返回 state、media_frame、written_frames、dropped_frames、pauses、segments 六项。SegmentInfo 含 index、start_frame、end_frame、frames、transition_frames、state；失败一律抛 RecordingError(code, detail)。

要查清的现象与边界：① 状态机只允许 idle 到 recording、recording 到 paused 或 stopped、paused 到 recording 或 stopped，非法迁移报 STATE_ERROR，状态与片段在每次迁移后保持一致；重复暂停与重复恢复按幂等处理，暂停计数不重复增加，状态不变；② 暂停次数达到 max_pauses 后继续暂停报 PAUSE_LIMIT，当前状态与片段都不变；③ 暂停期间到达的帧全部丢弃且不推进媒体时间，恢复后时间戳从暂停点继续而不是按墙钟跳变；④ 录制帧按媒体帧推进，成功写入返回真并推进媒体帧与 written_frames，单段达到 max_segment_frames 自动收尾并原位置开启下一段；⑤ 片段数达到 max_segments 后再要开新段报 SEGMENT_LIMIT，该帧不写入、媒体时间不前进；⑥ 恢复时可指定过渡帧数，默认取构造参数；过渡让媒体时间前跳并记入新段的 transition_frames，为零则严格续接；⑦ 停止时把打开中的片段收尾为 complete，打开中的片段状态为 open，片段起止用左闭右开表示，停止后不再接受帧；⑧ 重启恢复只在 idle 状态接受，接受清单时校验序号连续、各段不重叠、已收尾段的字节数等于帧数乘 bytes_per_frame，清单里的序号、起止与字节数必须互相自洽，清单条目数同样受 max_segments 限制，违规分别报 CORRUPT_MANIFEST 与 SEGMENT_LIMIT，失败时整份状态不动；⑨ 重启时最后一段若未正常收尾，标记为待修复并按已有字节截断到整帧，不把半帧写进清单；⑩ 连续暂停恢复产生的极短片段并入前一段，短于 min_segment_frames 的片段不单独出清单；⑪ 状态迁移与写帧必须加锁，八路并发暂停只有一次真实迁移，多路并发写帧不丢不重；快照是深拷贝，恢复后状态、媒体位置、片段清单与计数都在，结构不全报 SNAPSHOT_INVALID 且原状态不动。

硬约束：本模块只做本地状态与清单，不落盘；任何时刻只允许一个有效片段处于打开状态；片段数、暂停次数、单段时长都是硬上限；恢复失败必须原子回退，不得留下半份清单。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: recording_pause_controller
- typeName: RecordingPauseController
