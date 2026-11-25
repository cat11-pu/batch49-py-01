# batch49-py-game-a-16

## 题面

季度版本复盘时，经济组发现制作熟练度的产出账对不上：同一批材料偶尔扣两次，暴击出的附加品在背包满时凭空消失，玩家一天刷满经验后等级还在涨，维护重启后整条熟练度曲线回到初始值。客服把最近两周的工单按配方、玩家和日期拉了透视表，异常集中在批量制作、暴击判定和跨日结算三个环节，单人复现时又时好时坏，怀疑是整批预占与逐件结算之间的账没有对齐。范围是 gsb/batch49-py-game-a-16 的 craft_mastery_engine.py，类 CraftMasteryEngine，按玩家维护配方、材料、熟练度经验、背包与溢出邮件。

公开接口：CraftMasteryEngine(*, daily_exp_cap=1000, backpack_capacity=20)；register_recipe(recipe_id, *, materials, base_exp, bonus_item=None)；add_item(player, item, count)；inventory_of(player)；craft_batch(*, player, recipe_id, count, now=None, double_exp=False, crit_rolls=None) 返回 CraftBatchResult；grant_exp(*, player, amount, now=None)；level_of(*, player, now=None)；exp_of(player)；crit_rate(*, player, now=None)；bonus_output(player)；mail_of(player)；claim_mail(*, player, mail_id)；daily_exp_used(*, player, now=None)；stats()；snapshot()；restore(state)。拒绝一律抛 CraftError(code, detail)。CraftBatchResult 含 crafted、failed、exp_gained、bonuses、mailed、capped 六个字段。

故障与边界：①配方登记要做完整校验：材料表为空、材料数量非正、基础经验非正、附加品为空串都必须拒绝，避免脏配方进入生产；②每级需要 100 点经验，单件经验为 base_exp // (等级 + 1)，最低 1 点，等级上限 20 级；③暴击率从 5% 起，每级加 1 个百分点，封顶 25%，按结算时的等级计算；④每件成品独立判定暴击，命中时按配方产出附加品，不能整批共用一次掷点；⑤暴击判定序列可用 crit_rolls 注入，长度必须等于件数，否则整批拒绝且不扣材料、不结算经验；⑥双倍经验消耗一个双倍经验道具，把本批经验翻倍，道具不足直接失败；⑦日经验上限按自然日累计，超出部分截断，结果里的 capped 标记为真，跨日自动归零；⑧同一玩家多线程结算经验不得丢失，锁粒度按玩家而不是整张配方表；⑨成品与附加品写入背包，背包有容量上限，容量按件数计算；⑩快照返回七个键：version、recipes、players、inventories、daily、stats、mail，且与内部状态不共享结构；⑪恢复只接受 version=1、七键齐全且类型正确的字典，成功后配方、经验、背包、邮件、当日计数全部一致，否则抛 CRAFT_SNAPSHOT_INVALID 且原状态保持不变；⑫统计返回 crafted、crits、exp_granted、mailed、claimed 五个计数，每笔操作只记一次。

硬约束：批量制作先按件数整批预占材料，中途材料不足时已完成件保留、未完成件不扣料并计入失败数；暴击附加品受背包容量限制，放不下时转邮件而不是丢弃；日经验达到上限后仍可制作，但不再获得经验，也不消耗双倍经验道具。

交付要求：实现放在上述模块文件内，公开签名保持不变，内部结构自行决定；快照键名必须与题面一致，恢复失败不能留下半套状态。

验收：python3 -m pytest -q tests 全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: craft_mastery_engine
- typeName: CraftMasteryEngine
