# batch49-py-game-c-11

## 题面

转码集群这周第三次在拼接环节卡住：一批长视频按关键帧切成多块并行转码，前几块很快回来，某一块迟迟没有结果，负责收尾的进程却已经把半成品当作完整片交出去，播放器一拖到接缝就花屏。另一路任务里相邻两块的起止时间戳对不上，硬拼后整条音轨提前了半帧；还有块在重转期间，旧版本结果被拿去占位，成片里夹进了上一轮的字幕。范围落在 gsb/batch49-py-game-c-11 的 chunked_transcode_joiner.py，类型 ChunkedTranscodeJoiner，负责切块计划、完成上报归位、边界校验、重试与最终拼接。

公开接口：ChunkedTranscodeJoiner(*, max_chunk_frames=300, max_chunks=64, max_retries=2, max_total_retries=5)；plan_chunks(total_frames, keyframes)；chunks()；mark_done(report)；verify_boundary(index)；retry_chunk(index)；chunk_state(index)；contiguous_done()；join()；progress()；snapshot()、restore(state)；stats()。progress 返回 planned、done、pending、needs_retry、failed 五项，chunk_state 取 pending、done、needs_retry、failed 四种，完整拼接时 reasons 为空元组，stats 返回 reports、duplicate_reports、joins、retries、restores 五项。ChunkSpec 含 index、start_frame、end_frame、key_aligned，ChunkReport 含 index、start_frame、end_frame、params_hash、keyframe_intact，JoinResult 含 complete、chunk_count、joined_frames、truncated_frames、reasons；拒绝一律抛 ChunkError(code, detail)。

要查清的现象与边界：① 切块必须在关键帧处下刀，关键帧列表必须严格递增、首项为 0 且都小于总帧数，不合规报 INVALID_KEYFRAMES，总帧数非正报 INVALID_DURATION；② 两个关键帧之间的区段超过 max_chunk_frames 时按上限强制再切，强制切出的块 key_aligned 为假，块序号从 0 连续编号且结束帧不含在块内；③ 计划块数超过 max_chunks 报 CHUNK_LIMIT 且不保留半份计划，任何一块已有完成上报后再改计划报 PLAN_LOCKED；④ 完成上报的序号必须落在计划内，越界报 UNKNOWN_CHUNK，结束帧与计划不一致报 SPAN_MISMATCH；成功与幂等重复都返回 done；上报可以乱序到达，按序号归位，contiguous_done 只数从 0 开始连续完成的前缀；同一块的同一份上报重复到达按幂等处理、不重复计数，换成不同的起止或参数指纹再报报 ALREADY_DONE；⑤ 所有块必须使用同一个参数集指纹，后到的指纹不一致报 PARAMS_MISMATCH 且该块保持未完成；⑥ 关键帧对齐的块上报关键帧不完整报 KEYFRAME_BROKEN，非关键帧对齐的块不因该标志被拒；⑦ 单块重试次数与全局重试预算分别有上限，超出分别报 RETRY_EXHAUSTED 与 RETRY_BUDGET，永久失败的块状态为 failed；⑧ 拼接要求所有块完成且首尾相接，未完成报 NOT_READY，没有计划报 NOT_PLANNED，接缝出现空洞报 BOUNDARY_MISMATCH 并把后块打回重转，verify_boundary 对首块在完成时返回真、对相邻块检查首尾相接与参数一致；⑨ 相邻块出现时间戳重叠时，按前块结束时刻截断后块开头，截断量超过一帧则不得硬拼而应回退重转；⑩ 某块重转期间后续块可能已完成，拼接必须等该块新版结果，不得拿旧版占位；⑪ 中间块永久失败时允许输出到前一块为止并标记不完整，已完成的后续块保留供续跑。

硬约束：同一实例的切块、上报、重试与拼接必须串行，八路并发上报同一批不同序号后计数准确，重复上报不产生额外副作用；快照必须是深拷贝，恢复后计划、上报、参数指纹、重试次数与失败状态都在，结构不全报 SNAPSHOT_INVALID 且原状态不动；块数、单块时长与重试次数都是硬上限，超限不得留下部分变更。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: chunked_transcode_joiner
- typeName: ChunkedTranscodeJoiner
