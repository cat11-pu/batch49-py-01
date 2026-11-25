# batch49-py-game-c-08

## 题面

直播频道启用低延迟分块后，播放器每几秒卡一次，追到日志发现播放列表把半截分片当 part 发了出去，刷新接口偶尔还超时无响应，观众侧的表现为起播慢、追帧失败，抓包能看到同一 part 被重复下载。处理范围是 gsb/batch49-py-game-c-08 的 ll_hls_part_publisher.py，类 LlHlsPartPublisher 负责把持续推入的分片切成可独立解码的 part，维护播放列表版本与阻塞刷新。这套发布链路同时服务网页与机顶盒两种播放器，发布列表按媒体序号与 part 序号排序，读方只关心某个版本之后有没有新 part。

公开接口：LlHlsPartPublisher(part_target_ms=500, min_part_ms=200, hold_back_parts=2, window_parts=6, max_pending_parts=64, blocking_timeout_ms=1000)；push_chunk(data, duration_ms) -> int；cut_part(media_seq) -> tuple[int,int] | None；publish(media_seq) -> dict；playlist(media_seq=None) -> dict；blocking_reload(after_version, timeout_ms=None) -> dict；preload_hint(media_seq=0) -> dict | None；hold_back() -> int；pending_count() -> int；stats() -> dict；snapshot() -> dict；restore(state) -> None。发布结果至少含 version、media_seq、gap、parts，parts 每项带 media_seq、part_index、duration_ms、size；错误抛 PartPublishError，code 用 PART_INVALID、MEDIA_SEQ_INVALID、SNAPSHOT_INVALID。

故障与边界：①推入空数据、非字节或非正时长当场拒绝；②缓冲累计不足 min_part_ms 时 cut_part 返回 None，不得生成空 part，游标停在缓冲里等下次切分；③每个媒体序号内 part 序号从 0 递增，换媒体序号后归零；④publish 的媒体序号只能前进，跳号时 gap 置真，回退报错；⑤同一媒体序号重复 publish 且没有新 part 时版本不变，新增可见 part 或推进媒体序号时版本加一，版本号只增不减；⑥hold-back 从最新 part 往前压住指定数量不发布，列表窗口只保留最近 window_parts 个 part；⑦preload_hint 给出下一 part 的媒体序号、序号与预计时长，缓冲为空返回 None，提示只做预取参考，不改变已发布列表；⑧blocking_reload 在版本已前进时立即返回当前列表，否则等新版本；⑨同一媒体序号同一 part 序号重复到达时，内容不同必须拒绝后者，内容相同按重复忽略；⑩待发布 part 数封顶，超限报错而不是覆盖旧 part；⑪part 跨越独立分片边界时优先在边界切分，剩余不足最小时长并入下一 part，不得发布空 part；⑫阻塞刷新在超时前没有新 part 时返回当前列表并带 timeout 标记，不返回错误也不挂起。

硬约束：发布与刷新并发时，读方只能看到完整列表版本，不得读到半更新的 parts；发布与切分都在同一把锁内完成；刷新等待由发布事件唤醒，不用忙轮询，超时时间按调用方传入值生效；快照带走已发布列表、待发布 part、缓冲与计数，restore 校验失败抛 SNAPSHOT_INVALID 且现有状态不变，恢复后 playlist 与 stats 和快照时一致。stats 至少给出 chunks、parts、published、reloads、timeouts 五个计数。

验收：python3 -m pytest -q tests 15 条全过，不改测试、不跳过、不按固定时长写死；并发发布与刷新、重启恢复自行验证；并发下播放列表版本单调，读方不得看到 part 序号回退，同一份输入重复回放应得到同一串 part；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: ll_hls_part_publisher
- typeName: LlHlsPartPublisher
