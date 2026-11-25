# batch49-py-game-a-07

## 题面

首领战结算连续两周被投诉：击杀榜第一名拿不到金档，公会里几个小号分摊了过量伤害后反而人人有奖，窗口关闭后到达的伤害还被算进上一场，跨角色参战的同一账号在榜上出现两次，客服按账号补发时又重复发了一遍，活动运营要求这周内给出可复现的结算结果并归档留痕。范围是 gsb/batch49-py-game-a-07 的 combat_contribution_settler.py，类 CombatContributionSettler，按战斗窗口归集伤害并结算贡献与奖励资格。

公开接口：CombatContributionSettler(min_damage=1, gold_share=0.5, silver_share=0.25)；open_window(battle_id, boss_hp, window_label="w1")、window_state(battle_id)、record_damage(battle_id, player_id, account_id, damage)、report_healing(battle_id, player_id, account_id, amount)、report_taken(battle_id, player_id, account_id, amount)、close_window(battle_id)、settle(battle_id)、eligibility(battle_id)、ledger(battle_id)、remaining_hp(battle_id)、stats()、snapshot()、restore(state)。拒绝操作抛 CombatSettlementError(code, detail)。窗口标识、战斗状态与结算缓存都以 battle_id 为键；close_window 返回含 raw_total 与 players 的汇总，players 每项含 raw 与 hits；ledger 每条含 player_id、account_id、damage 与递增序号；settle 返回字典，含 battle_id、ranking、overkill_total、remaining_hp 与 settled 字段，ranking 每一行包含 account_id、damage、share 与参战角色元组；stats 至少含 windows_opened、windows_closed、battles_settled、damage_entries。

故障与边界：①open_window 返回由战斗标识与窗口标签用冒号拼成的窗口标识，状态为 open，剩余血量等于首领血量；②同一战斗已有打开窗口报 WINDOW_ALREADY_OPEN，血量非正报 INVALID_BOSS_HP；③只有窗口打开时才能记伤害，否则 WINDOW_NOT_OPEN，非正整数伤害报 INVALID_DAMAGE；④窗口关闭后到达的伤害要写进台账留痕，但不得参与本次结算；⑤close_window 按角色汇总原始伤害与命中次数，重复关闭报 WINDOW_CLOSED；⑥settle 只能在窗口关闭后调用，否则 BATTLE_NOT_CLOSED；⑦结算按账号合并角色后按有效伤害降序排名，同分按账号标识升序；⑧过量伤害按各玩家最后一击的先后顺序分摊剔除，不得按人数平均摊掉；⑨总伤害超过首领血量时剩余血量记 0，过量部分单独统计；⑩结算对同一 battle_id 幂等，重复调用返回首次结果，台账不重复增长；⑪治疗量与承伤量只影响奖励档位，不改变伤害排名，奖励资格按有效伤害占榜首的比例分 gold、silver、bronze 三档，低于 min_damage 的不入档；⑫快照结构不合法报 INVALID_SNAPSHOT，恢复后窗口、台账、结算结果都要还原。

硬约束：同一 battle 的记伤害与关窗可能并发，每条伤害只能落进打开或关闭的一侧，不能半条进结算；结算结果按 battle 缓存，重复查询走缓存，不允许每次全量重算；台账按命中序号严格递增，快照恢复后序号不能回退或重号。统计口径以有效伤害为准，原始伤害只用于复盘；重复上报的同一次命中要按序号去重，不能算成两次伤害。

验收：python3 -m pytest -q tests 的 15 条用例全部通过，不得改测试、跳过或按固定输入写死；并发记录与快照恢复自行验证；只做本地实现，不改动上游模块，所有接口只依赖标准库。交付源码、测试输出与一页以内的结算口径说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: combat_contribution_settler
- typeName: CombatContributionSettler
