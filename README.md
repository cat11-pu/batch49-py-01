# batch49-py-game-a-28

## 题面

公会团本的掉落结算这周出了争议：同一个击杀里，A 玩家吃到保底，B 玩家的保底进度也跟着清零；三人组队的加成看起来只写在公告文案里，实际掉率没有任何变化；还有成员掉线重连后计数被清空，补偿一直算不出来；组队界面显示的人数与实际参与结算的人数也对不上。客服把三份战斗记录摆在一起，才发现每名成员原本各有一份计数，结算时却被并成了一份。范围是 gsb/batch49-py-game-a-28 下的 party_pity_isolator.py，类 PartyPityIsolator，负责成员独立保底计数与团队掉落加成。

公开接口：PartyPityIsolator(*, pity_threshold=20, base_rate=200, per_member_bonus=50, max_rate=500, max_counter=999)；register_member(party_id, member_id)；set_offline(party_id, member_id, offline=True)；reset_member(party_id, member_id)；record_kill(kill_id, party_id, *, rolls=None, now=None)；roll_for(kill_id, member_id)；bonus_rate(party_id)；member_state(party_id, member_id)；snapshot()；restore(state)；stats()。掉率以千分数表示，roll 为 0 到 999 的整数，严格小于掉率才算命中；rolls 为 member_id 到整数的映射，未提供者按 1000 处理，即不靠 roll 掉落。错误抛 PartyPityError(code, detail)：PARTY_INVALID_REQUEST、PARTY_MEMBER_EXISTS、PARTY_UNKNOWN、PARTY_UNKNOWN_MEMBER、PARTY_STATE_INVALID。MemberState 含 party_id、member_id、order、pity、kills、offline；MemberDrop 含 member_id、rarity、pity_before、pity_after；stats 有 kills、drops、normal_drops、pity_drops、resets 五个计数，构造时全部为零。

需要处理的情况：① 成员按注册顺序编号，重复注册抛 PARTY_MEMBER_EXISTS；② 每次击杀对每名在线成员计数加一，结果按注册顺序每条成员一条记录，同一击杀里每名成员只出现一次，未掉落时 rarity 为 None；③ 计数加一后达到阈值即判 pity，rarity 为 pity 且计数归零，未达到时按 roll 小于团队掉率判 normal；④ 团队掉率为 base_rate 加上（在线人数减一）乘 per_member_bonus，封顶 max_rate，在线人数只统计未掉线成员，人数变化从下一场击杀起生效，在线人数不足一人时按一人计算；⑤ 掉线成员不参与本次掉落，其计数既不增加也不清零；⑥ 同一 kill_id 重复上报只结算一次，后到的成员不补发；⑦ 计数达到 max_counter 后不再增长，也不会因此触发保底；⑧ reset_member 清零计数并返回清零前的数值，计入 resets；⑨ roll_for 返回该次击杀中某成员的结算记录，未参与者返回 None；⑩ 任一 roll 非法时整场击杀不结算，成员计数保持不变；⑪ 快照深拷贝，恢复时结构不合法抛 PARTY_STATE_INVALID 且现有状态不动。

硬约束：多线程同时上报不同 kill_id 时同一队伍的计数必须准确累加、不丢结算，成员状态与计数要在锁内成对更新；复盘要求同一份输入重放得到完全一致的结果；快照恢复后成员、计数、击杀记录与统计都要还原且与快照不共享引用；计数上限为硬约束，掉线状态也要随重启还原。

验收：`python3 -m pytest -q tests` 共 15 条必须全过，不得改测试、跳过或按固定输入写死；并发累加、掉线跳过、千分位边界与重启恢复都要自己构造场景验证；只做本地实现，不引入第三方依赖。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: party_pity_isolator
- typeName: PartyPityIsolator
