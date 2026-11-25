# batch49-py-game-a-02

## 题面

停服维护结束后，几个游戏大区的角色存档集体回档：玩家下线前刚拿到的装备没了，客服后台还能看到上一代存档躺在磁盘上，客户端却只读到一份空的 active。更麻烦的是，进程在写档途中被运维强杀，重启后同一账号冒出两份都自称最新的存档，读到哪份全看目录扫描顺序，客服希望能一眼看出哪一代是坏的、哪一代还能回滚。范围锁定 gsb/batch49-py-game-a-02 的 save_slot_rotator.py，类型 SaveSlotRotator，按账号维护 active、previous、backup 三代槽位，写入走临时槽加校验再原子晋升。

磁盘上的存档是规范化 JSON，另带一份 CRC32 校验和与写入字节数。一次保存先落临时槽，内容写全且校验通过后才晋升；晋升时旧 active 降为 previous，旧 previous 降为 backup，更老的一代直接丢弃。临时槽可能只写了一半，也可能写全了但没来得及打晋升标记，进程重启后必须能把这两种残留和正常待晋升的临时槽区分开。

公开接口：SaveSlotRotator(*, slot_limit=3, max_slot_bytes=65536)；stage(account, payload, *, complete=True, checksum=None)；mark_promotion(account, temp_id)；promote(account, temp_id)；save(account, payload)；load(account, *, tier="active")；rotate(account)；recover(account)；prune(account, *, keep_candidates=0)；pending(account)；slot_meta(account)；generation(account)；adopt_slot(account, tier, payload, checksum, *, generation=0)；snapshot()、restore(state)、stats()；模块级 slot_crc(payload)。SlotMeta 含 account、tier、generation、checksum、size、status 六个字段，TempRecord 含 account、temp_id、checksum、size、complete、promotion_marked，stats 返回 saves、promotions、recoveries、pruned、corrupt_loads 五项，pruned 按删除条数累计，其余按次累计，slot_meta 返回 tier 到 SlotMeta 的字典。拒绝一律抛 SlotFault(code, detail)。

要查清的现象与边界：① 校验和取规范化 JSON（键排序、无多余空白、UTF-8 字节）的 CRC32，输出 8 位小写十六进制，槽位记录同时保存字节数；② 同一账号最多保留 slot_limit 个未晋升临时槽，超限报 SLOT_CAPACITY，单槽字节数受 max_slot_bytes 硬顶，超限报 SLOT_TOO_LARGE 且不产生临时槽；③ stage 默认写完整内容并算校验和，complete=False 表示内容不完整；④ promote 要求临时槽内容完整且已打晋升标记，否则报 SLOT_NOT_READY，晋升前重算校验和，不符报 SLOT_CHECKSUM_MISMATCH；⑤ 每次晋升 generation 加一且单调递增，save 一次只晋升一次，八路并发保存后代号正好取 1 到 8 各一次；⑥ 临时槽内容完整但没打晋升标记时，recover 只能把它留作候选，不得直接当成 active；⑦ recover 对内容不完整的临时槽直接删除并计数，对完整且已标记的临时槽校验通过后晋升，返回 promoted、discarded、candidates 三个计数；⑧ 回滚到 previous 之后 generation 必须继续递增，不能复用被回滚掉的旧编号；⑨ backup 校验失败只把 backup 标为不可用，不影响 active 加载与一次回滚；⑩ load 缺档报 SLOT_UNAVAILABLE，tier 只认 active、previous、backup，其余报 SLOT_INVALID_TIER，校验和不符报 SLOT_CORRUPT 并把状态置 corrupt；⑪ adopt_slot 只登记磁盘索引不做校验，登记后状态为 unverified，正常保存的槽位状态为 ok，校验留给 load；⑫ rotate 手动轮转，active 降 previous、previous 降 backup、active 置空，代号加一，返回仍存在的各代代号；prune 按保留数量清理临时槽并返回删除条数。

硬约束：同一账号的并发保存必须串行，每次保存只晋升一次且代号不重号；进程重启后凭临时槽的完成标记与晋升标记恢复，候选槽不得被误当成 active；快照是深拷贝，恢复后三代内容、代号、校验和、状态、临时槽都在，结构不全报 SLOT_INVALID_STATE 且原状态不变；临时槽数量与单槽字节数都是硬上限。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: save_slot_rotator
- typeName: SaveSlotRotator
