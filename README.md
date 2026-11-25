# batch49-py-game-c-13

## 题面

同一段环境音在两条总线上听感对不上：一条总线临时旁路了压缩器，声音却还是晚了十几毫秒；另一条总线把混响挪到链尾后，空间感突然断掉，像是内部缓冲被清空。更糟的是制作人删掉一个带长尾的效果后立刻插了新的，旧尾音压在新效果上，导出直接爆音。范围落在 gsb/batch49-py-game-c-13 的 effect_insert_chain.py，类型 EffectInsertChain，负责每条总线的有序效果插入链、增删、重排、单槽旁路、尾音冲刷与分块处理。

公开接口：EffectInsertChain(*, slots_per_bus=4, max_buses=8, block_size=64, max_tail_frames=200)；add_bus(bus_id)；register_effect(effect_id, *, latency_frames, tail_frames=0)；insert(bus_id, effect_id, *, position=None)；remove(bus_id, effect_id, *, force=False)；move(bus_id, effect_id, new_position)；bypass(bus_id, effect_id, bypassed=True)；flush_tail(bus_id, *, effect_id=None, frames=0)；process_block(bus_id, frames)；order(bus_id)；latency(bus_id)；snapshot()、restore(state)；stats()。SlotView 含 bus_id、position、effect_id、bypassed、latency_frames、state，state 取 active、bypassed、flushing 三者之一，RemoveResult 含 effect_id、state、tail_remaining、position，ProcessResult 含 bus_id、frames、active、bypassed、flushing、latency_frames；拒绝一律抛 EffectError(code, detail)。

要查清的现象与边界：① 每条总线槽位数、总线数、单块处理帧数与效果尾音时长都有硬上限，超限分别报 SLOT_LIMIT、BUS_LIMIT、BLOCK_TOO_LARGE、TAIL_LIMIT，且不改动已有槽位；② 效果必须先在全局注册表登记，重复登记报 EFFECT_DUPLICATE，负延迟或负尾音报 INVALID_EFFECT，总线不存在报 UNKNOWN_BUS，效果未登记报 UNKNOWN_EFFECT；③ 插入默认追加，指定位置时按新位置落槽并把后面整体后移，位置越界报 POSITION_RANGE，同一总线重复插入同一效果报 EFFECT_IN_CHAIN；④ 旁路只改开关，不改变链顺序与槽位编号，但旁路槽仍占用延迟补偿并保持内部状态，取消旁路后从原状态继续而不是重置，不在链内的效果不能旁路，报 EFFECT_NOT_IN_CHAIN；⑤ 重排把效果移到新下标，链顺序按新位置整体让位，移动后其内部缓冲不清空，但延迟补偿按新位置重算并通知下游，越界与不在链内分别报 POSITION_RANGE、EFFECT_NOT_IN_CHAIN；⑥ 尾音为 0 或显式 force 时移除立即释放槽位，返回 removed；否则槽位进入冲刷态，效果仍占位置，返回 flushing 与剩余尾音帧数，移除不在链内的效果报 EFFECT_NOT_IN_CHAIN；⑦ 冲刷按 flush_tail 与 process_block 推进，进度以帧为单位保留在槽位上，剩余到 0 时自动释放槽位，重复冲刷已完成的效果报 NOT_FLUSHING，单块处理帧数不得超过 block_size，非正帧数报 INVALID_FRAMES；⑧ 移除正在冲刷尾音的效果时禁止同槽立即插入新效果，需等尾音结束或显式强制；⑨ 总延迟为链上所有槽位的延迟之和，process_block 在块边界冻结链结构，单块内增删不得影响本块统计；⑩ 快照是深拷贝，恢复后链顺序、旁路标记、冲刷进度与延迟都对得上，结构不全报 SNAPSHOT_INVALID 且原链不动；同一总线的链变更与处理必须串行，多路并发插入后槽位不重不漏。

硬约束：链结构变更与分块处理并发时按块边界切换，单个块内链结构不变；槽位数、尾音时长与处理块大小都是硬上限；被拒的增删、旁路与移动都必须保持原链、原延迟和原冲刷进度。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: effect_insert_chain
- typeName: EffectInsertChain
