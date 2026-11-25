# batch49-py-game-b-14

## 题面

开放世界骑载具高速穿城时，前方街区的贴图来不及解码，画面先糊再突然弹出；掉头回去时旧预取还占着解码槽，新场景只能排队等。复现时把相机速度调到 0 再原地转头，预取队列会彻底停住，可见扇区一个都不解码，镜头一转就白屏。改动范围限定在 gsb/batch49-py-game-b-14 的 texture_prefetch_queue.py，类型 TexturePrefetchQueue，公开签名保持不变：TexturePrefetchQueue(*, max_inflight=4, max_ready=32, upload_bytes_per_frame=4096, lookahead=2)；set_world(sector_count)；set_pose(x, y, heading_deg, speed)；predict_sectors()；enqueue(sector_id)；next_decode()；mark_ready(sector_id, size_bytes)；cancel_stale(keep)；upload(sector_id)；begin_frame()；ready()；evictable()；budget()；snapshot()；restore(state)。错误一律抛 TextureError(code, detail)。

需要修好的行为：

1. set_world 只接受 1 到 4096 的扇区数，否则报 TEXTURE_INVALID_WORLD；未设世界就调 set_pose 报 TEXTURE_NO_WORLD；
2. set_pose 的坐标与朝向必须是有限数、速度不得为负，否则报 TEXTURE_INVALID_POSE；未设位姿就预测报 TEXTURE_NO_POSE；
3. 扇区按 360 度均分，当前扇区为朝向除以扇区角取整后对扇区数取模；速度为正时预测从当前扇区起连续 lookahead 个后继扇区，按由近到远返回，后继扇区按编号递增方向计算，越过末尾回到 0 号扇区；
4. enqueue 对超范围扇区报 TEXTURE_INVALID_SECTOR，已在途或已排队的扇区返回 False，成功返回 True，入队顺序不要求与扇区号一致，重复入队不得改变队列长度；
5. next_decode 在在飞数达到 max_inflight 时返回 None，否则按当前朝向的角距离、再按扇区号取最小者出队并记为在飞；
6. mark_ready 只接受在飞扇区，否则返回 False；就绪表加入新条目前若已满 max_ready 条，先淘汰最早入表的条目，淘汰只发生在就绪表，正在解码的扇区不受影响；
7. cancel_stale 只移除排队中不在保留集合里的请求，并按扇区号升序返回被取消的扇区，返回列表不含仍在途的扇区，已经开始的解码不算取消；
8. upload 只接受就绪条目，否则报 TEXTURE_NOT_READY；成功时扣减本帧剩余上传预算并返回字节数，同一帧内按调用顺序扣减；
9. begin_frame 把上传预算重置为 upload_bytes_per_frame 并累加帧号；
10. budget 返回 frames、queued、inflight、ready、upload_remaining 五个计数；snapshot 返回的字典包含 version、world、pose、queue、inflight、ready、counters，其中 queue 按优先级排出扇区号，inflight 是扇区号列表，ready 按入表顺序存 (扇区号, 字节数)，counters 含 frames 与 upload_remaining，pose 用 (x, y, 朝向, 速度) 四元组表示，version 固定为 1；restore 遇到结构不符报 TEXTURE_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：队列操作加锁，同一扇区只保留一个在途请求；同一扇区先被预取后又因相机折返被取消时，若解码已开始则允许完成并标记为可淘汰，不得把结果直接丢弃；预测速度为零时退化为按当前朝向预取一圈，而不是停止预取；上传预算耗尽时就绪条目留在表内等待下一帧，不得按超出预算丢弃；在飞数、就绪条目数与每帧上传字节都有上限硬顶；队列、在飞表与就绪表必须一起更新，任何一次操作都不得留下半条记录。

验收：python3 -m pytest -q tests 15 条全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: texture_prefetch_queue
- typeName: TexturePrefetchQueue
