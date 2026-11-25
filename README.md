# batch49-py-game-a-04

## 题面

版本更新后，玩家背包被一键整理搅乱：同样的药水明明能叠成一组，整理完还是散在三格；更严重的是部分玩家点完整理，背包里的物品凭空少了几件，客服翻后台只能看到整理前后的快照对不上。范围锁定 gsb/batch49-py-game-a-04 的 inventory_compactor.py，类型 InventoryCompactor，把整理拆成先生成搬移指令、再整批原子执行两步。

背包有固定格位，每格最多放一种物品的一个品质档；整理要按运营配置的优先级把物品重排到低编号格位，同组堆叠在容量内合并，整理过程不能直接改玩家背包。目标格被占用时得先把它腾到空槽，整批指令要么全成要么全不成；每次整理都要能算出搬了几步、整理前后数量是否守恒。运营还要求每次整理都能回放，出了问题可以按指令逐条复盘是哪一步把物品放错了位置。

公开接口：InventoryCompactor(*, slot_count=8, max_moves_per_item=2)；set_layout(slots)；layout()；set_priority(order)；set_stack_limit(item_id, limit)；plan_moves()；apply_moves(moves=None)；compact()；abort_plan()；verify_conservation(before, after)；snapshot()、restore(state)、stats()；Stack 含 item_id、quality、quantity，Move 含 from_slot、to_slot、quantity。拒绝一律抛 CompactorError(code, detail)。

要查清的现象与边界：① set_layout 只接受 0 到 slot_count-1 的格位、正数量与非空物品名，否则报 COMPACT_INVALID_LAYOUT；② 优先级列表必须恰好覆盖背包里出现的物品，缺失或重复报 COMPACT_INVALID_PRIORITY；③ 目标布局按优先级顺序为每个（物品，品质）分组分配格位，组内按品质升序，从 0 号格开始连续占用，所需格位超过 slot_count 报 COMPACT_CAPACITY；④ 每组数量按 set_stack_limit 切分，溢出部分占用下一个目标格，未单独设置上限的物品按 99 计；⑤ plan_moves 只产出指令、不改布局，每条指令执行时目标格必须为空或同物品同品质且合并后不超上限，否则报 COMPACT_ILLEGAL_MOVE；⑥ 指令条数取最少，两个堆叠互换时借空槽，不允许无意义的二次搬运，同一物品的搬移次数受 max_moves_per_item 约束，超预算报 COMPACT_BUDGET；⑦ 同物品但品质档不同的堆叠即使未满也不得合并，只能各自压缩；⑧ 整理计划必须稳定，同优先级物品保持原有相对次序，不能按格位号重排；⑨ apply_moves 整批原子，任一指令失败时已搬移部分必须回滚到整理前布局，无待执行计划时报 COMPACT_NO_PLAN，abort_plan 清掉计划；⑩ compact 等于 plan_moves 加 apply_moves，返回的字典含 moves 与 layout 两项，整理前后按（物品，品质）核对数量守恒；⑪ 快照深拷贝，恢复后布局、优先级、堆叠上限与计数都在，结构不全报 COMPACT_SNAPSHOT_INVALID 且原状态不变；⑫ stats 返回 plans、moves_applied、aborts 三项，compact 内部生成计划也计入 plans。

硬约束：整批搬移必须原子，中途任一步失败不能留下搬了一半的背包；同一物品的搬移预算是硬上限，超了宁可拒绝整理也不硬搬；格位数量与单格堆叠上限都是硬顶，计划生成必须是确定性的，同样输入两次整理得到同一份指令。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: inventory_compactor
- typeName: InventoryCompactor
