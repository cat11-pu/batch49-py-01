# batch49-py-game-a-03

## 题面

周末活动结束后，玩家投诉背包里的烤鸡分成了两堆，一堆三天后过期、一堆下周过期，系统却把两堆算成一堆，吃掉两只后剩下的全变成即将过期的；另一批玩家把药剂塞进仓库，格位明明显示已占满，整理时又发现过期的药剂还占着位置清不掉。范围锁定 gsb/batch49-py-game-a-03 的 perishable_stack_ledger.py，类型 PerishableStackLedger，按入库批次记录可堆叠消耗品的到期时间、扣减与清理。

背包里同一格只能放同一种物品，但同一种物品可以来自不同入库批次，到期时间各不相同；系统用整数刻度表示到期时刻，容差窗口内视为同一批。玩家操作有先后，扣减要按最早到期先出，过期清理只能由定时任务触发，查询不能顺手把东西删掉。运营还要求同账号下多个格子互不影响，坏掉一批不能拖累整包，清理过期时要能给出可读的对账清单。

公开接口：PerishableStackLedger(*, stack_limit=20, cell_capacity=8, tolerance=2)；add_batch(cell, item_id, quantity, expires_at)；consume(cell, item_id, quantity, *, now)；split_batch(cell, item_id, expires_at, quantity)；merge_batches(cell, item_id, expires_a, expires_b)；expire_due(now)；inspect(cell, item_id=None)；cell_usage()；total_quantity(cell, item_id)；snapshot()、restore(state)、stats()。Batch 含 cell、item_id、expires_at、quantity、batch_id 五个字段。拒绝一律抛 LedgerError(code, detail)。

要查清的现象与边界：① add_batch 校验数量并返回落库后的批次，数量必须为正，0 或负数报 LEDGER_INVALID_QUANTITY，同物品在容差窗口内的新批次并入到期更早的那一批，窗口外另立一批；② 单格同物品总量受 stack_limit 硬顶，超限报 LEDGER_STACK_LIMIT 且整次入库不生效；③ 整包格位按 cell_capacity 硬顶，新格子超限报 LEDGER_CAPACITY；④ consume 按到期时间从早到晚扣减，返回（到期时间，扣减数量）列表，可跨多个批次；⑤ 已到期批次不参与扣减，数量不够报 LEDGER_SHORTAGE 且本次扣减整体不生效；⑥ 部分扣减后剩余批次必须保留原到期时间，不得刷新为操作时刻；⑦ split_batch 从指定批次拆出数量，新批次沿用原到期时间，数量不合法报 LEDGER_INVALID_SPLIT；⑧ merge_batches 只在容差窗口内合并，合并到到期更早的一批并返回该批次，总量超 stack_limit 报 LEDGER_STACK_LIMIT，到期时间相差超过容差即使同物品也拒绝合并；⑨ expire_due 只清理到期批次，返回（格子，物品，数量）并按格子、物品、到期时间排序，累计过期数量进 stats，查询与格位统计不得隐式删除；⑩ 未到期批次在格位容量统计中仍占位，过期清理只回收已到期批次；⑪ inspect 返回批次副本并按到期时间、物品排序，空格返回空列表；⑫ 快照深拷贝，恢复后批次与计数都在，结构不全报 LEDGER_SNAPSHOT_INVALID 且原状态不变，stats 返回 added、consumed、expired、splits、merges 五项，前三项按数量累计，后两项按次数累计。

硬约束：同一格并发扣减必须持同一把锁，八路各扣两只只能成功五次，剩余数量不得为负；查询、格位统计与过期清理严格分工，只有 expire_due 能删批次；单格总量与整包格位数量都是硬上限。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: perishable_stack_ledger
- typeName: PerishableStackLedger
