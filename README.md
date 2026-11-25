# batch49-py-game-a-08

## 题面

竞技场上了新反弹词条后，法系首领被自己的反伤弹死，护盾转移链把同一次伤害绕回原点，溢出伤害又被二次分配，日志里同一个 hit 出现两条结算记录，还有玩家的伤害没有归到原始来源，相关工单已经堆到下周。范围是 gsb/batch49-py-game-a-08 的 damage_reflect_router.py，类 DamageReflectRouter，负责反弹、转移与最终归属。

公开接口：DamageReflectRouter(default_frame_cap=1000000)；register_target(target_id)、register_reflect(owner_id, ratio, single_cap, frame_cap=None)、unregister_reflect(owner_id)、register_redirect(owner_id, target_id, priority=0)、unregister_redirect(owner_id, target_id=None)、route(hit_id, source_id, target_id, amount, frame=0)、settle(hit_id)、attribution(hit_id)、ledger(frame=None)、frame_total(owner_id, frame)、reset_frame(frame)、stats()、snapshot()、restore(state)。拒绝操作抛 ReflectRoutingError(code, detail)。route 返回的记录包含 hit_id、source_id、original_target、final_target、amount、reflected_to、reflected、dropped、chain 与 frame；attribution 返回 source_id、original_target、final_target、reflected_to 与 reflected；stats 至少含 routed、reflected 与 redirected 三个计数。所有入口都要求调用方传入显式标识：hit_id 由战斗帧与命中序号拼接，转移链和反弹词条都按 owner_id 索引，未登记的标识一律拒绝，不能隐式创建。

故障与边界：①register_reflect 的 ratio 必须在 0 与 1 之间（含 1），单次与每帧上限非负，否则 INVALID_REFLECT；②route 对同一 hit_id 幂等，重复调用返回首次结果，不得重复记帧；③未知承接目标报 UNKNOWN_TARGET，转移的发起方与承接方都必须先登记，非正伤害报 INVALID_DAMAGE；④无词条时伤害原样落到目标，结算记录保留原始来源；⑤反弹按 ratio 向下取整后回敬攻击者，接收方仍承受全额伤害，攻击者为空或与承接者相同则不反弹；⑥单次反弹上限截断超出部分，截断量记在 dropped；⑦同一来源同一帧的反弹按每帧上限累计，达到上限后不再反弹；⑧转移按优先级从高到低选择承接者，同优先级按目标标识升序取第一个，不得依赖字典遍历顺序；⑨反弹产生的伤害不得再次触发同一来源的反弹；⑩转移链出现环时截断到最后一个合法承接者，剩余伤害归还原始目标；⑪上限截断顺序固定为先单次上限再每帧上限，溢出不得二次分配；⑫快照结构不合法报 INVALID_SNAPSHOT，恢复失败时原状态保持不变，恢复成功后词条、台账与每帧累计都要还原。

硬约束：同一 hit_id 可能被多个结算协程同时提交，只能生成一条记录，读方不得看到半条；每帧累计按（来源、帧）隔离，换帧或 reset_frame 后不得沿用上一帧余量；快照恢复后转移优先级、反弹上限、台账顺序与每帧累计必须逐项还原，且不得与快照共享可变结构。单帧累计表按帧号回收，历史帧不得无限增长；同一来源在帧内累计到上限后返回零，不得报错中断结算。

验收：python3 -m pytest -q tests 的 15 条用例全部通过，不得改测试、跳过或按固定输入写死；并发提交与快照恢复自行验证；只做本地实现，所有接口只依赖标准库。交付源码、跑测记录与一页以内的路由口径说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: damage_reflect_router
- typeName: DamageReflectRouter
