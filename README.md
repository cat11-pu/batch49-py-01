# batch49-py-game-a-20

## 题面

游戏版本更新把「百人斩」门槛从 50 调到 80 后，客服收到两类工单：一类玩家说自己早就拿到的称号被系统收回，另一类说更新后同一份奖励邮件收到两遍；更糟的是重算任务中途被运维重启，再跑时从头开始且重复补发。运营同学手工比对发现，重算脚本每重启一次，补发数量就翻一倍，而时间窗类成就的判定结果和玩家实际达成日期对不上。更奇怪的是，有些玩家的达成时间明明在上个月，重算后却被算进了本周的时间窗。范围是 gsb/batch49-py-game-a-20 的 achievement_recompute_engine.py，类 AchievementRecomputeEngine，按玩家事件历史重算版本化成就规则。

公开接口：AchievementRecomputeEngine(*, batch_size=100)；register_rule(rule_id, *, version, metric, threshold, reward, window_seconds=None) 返回 Rule；record_event(*, player, metric, amount, occurred_at)；progress(*, player, rule_id)；evaluate(*, player)；grant(*, player, rule_ids)；recompute(*, players, max_batches=None) 返回 RecomputeReport；achievements_of(player)；pending_players()；stats()；snapshot()；restore(state)。拒绝抛 AchievementError(code, detail)。Rule、GrantRecord、RecomputeReport 均为 dataclass。

故障与边界：①规则编号、指标、奖励不得为空，版本从 1 开始，阈值与时间窗必须为正；②同一规则同一版本只能登记一次，新版本登记后旧版本不再参与评估，规则版本不能回退；③事件金额为正整数，事件带发生时间，按玩家与指标累计；④progress 统计当前版本规则对应指标的数量，时间窗规则取事件时间上任意窗口的最大值，没有事件时进度为零且不报错；⑤evaluate 返回已达成规则编号，按字典序排序；⑥grant 只针对已达成的规则，按（玩家、成就、规则版本）幂等；⑦补发记录包含玩家、规则编号、版本与奖励；⑧recompute 按玩家分批处理，报告 processed、granted、remaining、batches，每批处理数量由 batch_size 决定，剩余批次留到下次调用；⑨同一规则版本对同一玩家只处理一次，重复调用不得重复发奖，也不允许跳过尚未处理的玩家；⑩pending_players 返回尚有未处理规则事件的玩家，按字典序排序；⑪快照含 version、rules、events、grants、processed、stats、next_seq 七个键，深拷贝且与内部状态不共享结构；⑫恢复非法结构抛 ACHIEVEMENT_SNAPSHOT_INVALID 且原状态不变。

硬约束：规则收紧导致原本达成的成就不再成立时，已发奖励保留但标记为历史达成；重算中断后从批次断点继续，已处理玩家不得重复补发；时间窗类条件以事件发生时间判定，不以重算执行时间判定。

交付要求：实现放在模块文件内，公开签名与 dataclass 字段名保持不变；批处理的断点状态要能随快照往返；规则版本号只增不减，补发记录不可修改；任何一批失败都要能从该批开头重跑；恢复失败时规则、事件与补发记录都不能丢。

验收：python3 -m pytest -q tests 全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: achievement_recompute_engine
- typeName: AchievementRecomputeEngine
