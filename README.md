# batch49-py-game-a-06

## 题面

限时任务链这周连出故障：玩家在第一阶段暂停后去买菜，回来任务直接判超时；有人读档后背包里多出两把本该回收的钥匙；还有玩家反复暂停卡时间，发现剩余时间越暂停越长；客服还复现出一边暂停一边读档时阶段莫名跳回上一关的情况，运营已经把这批工单标成最高优先级。范围是 gsb/batch49-py-game-a-06 的 timed_quest_checkpoint.py，类 TimedQuestCheckpoint，负责多阶段限时任务链的阶段计时、暂停冻结与检查点回退。

公开接口：TimedQuestCheckpoint(stages, chain_id="chain")，stages 为（阶段标识，时长毫秒）序列；enter_stage(stage_id)、finish_chain()、pause(now_ms)、resume(now_ms)、tick(now_ms)、remaining_ms()、paused_total_ms()、checkpoint()、fail_and_rewind(reason="manual")、state()、grant_temp_item(item_id, qty=1)、grant_reward(item_id, qty=1)、inventory()、snapshot()、restore(state)。拒绝操作抛 QuestTimerError(code, detail)。所有状态查询返回同一份结构，字段包含 chain_id、chain_state、stage、stage_index、remaining_ms、paused、frozen_total_ms 与 failures；chain_state 取 idle、running、rewound、completed，整链完成后 stage 为 None。enter_stage、finish_chain、pause、resume、tick、checkpoint 都返回当前状态字典，fail_and_rewind 返回含 reason 与 failures 的记录，inventory 返回 temp 与 rewards 两个映射。

故障与边界：①进入阶段开始计时，剩余时间等于该阶段时长，阶段名与序号正确；②tick 按调用方传入的单调毫秒累计，重复 tick 同一时刻不得重复扣时；③暂停期间 tick 不推进，剩余时间保持不变；④恢复后从冻结处的剩余时间继续，单次暂停时长计入冻结总时长；⑤同一阶段多次暂停时冻结时长累加，不得重置阶段计时；⑥进入下一阶段必须按声明顺序，跳阶段报 STAGE_ORDER，重复进入当前阶段报 STAGE_ACTIVE；⑦暂停中禁止推进，报 QUEST_PAUSED；⑧暂停跨越阶段截止时刻时，恢复必须先判定超时再允许推进；⑨计时越过截止时刻，整链回退到最后检查点，阶段与剩余时间一起复位，失败次数加一；⑩checkpoint 只保存阶段入口快照，回退后该阶段从满时长重开，并撤销该阶段发放的临时道具、保留检查点之前的永久奖励；⑪finish_chain 结束最后阶段后状态为 completed，再推进报 CHAIN_COMPLETED；⑫快照结构不合法报 INVALID_SNAPSHOT 且原状态不变；临时道具与永久奖励分开记账，同一标识数量累加。

硬约束：计时只认传入的单调毫秒，时钟倒退必须报 CLOCK_BACKWARDS，不能静默接受；pause、resume、tick 可能来自不同线程，阶段、剩余时间与暂停标记的切换要加锁，任一时刻读到的状态必须属于同一版本；快照恢复后阶段、剩余时间、冻结累计、背包与失败计数都要还原，且不得与快照对象共享可变结构。检查点之前已经结算的永久奖励不能在回退时被反向收回，临时道具则必须随失败阶段一起清空。

验收：python3 -m pytest -q tests 的 15 条用例全部通过，不得改测试、跳过或按固定输入写死；并发暂停与快照恢复自行验证；只做本地实现。交付源码、跑测记录与一页以内的计时口径说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: timed_quest_checkpoint
- typeName: TimedQuestCheckpoint
