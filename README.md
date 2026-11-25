# batch49-py-game-a-19

## 题面

运营后台的日榜与周榜同时出现幽灵成绩：上周的分数还挂在今天的榜单上，同一玩家在同一个窗口反复刷屏，周一凌晨切窗时的成绩有时算进旧榜，服务重启后榜单与历史最佳全部清零。值班同学把日志按小时切开，发现过期剔除和切窗结算互相踩踏，同一批成绩在日榜与周榜里各算了一遍；客服抽查了十条记录，发现同一条成绩在两个榜单的窗口键上各有一份，清理时只删掉其中一份。还有玩家反馈，自己上周打出的最高分在周榜消失后，个人历史最佳也跟着归零。范围是 gsb/batch49-py-game-a-19 的 rolling_leaderboard.py，类 RollingLeaderboard，维护日榜与周榜的滚动窗口。

公开接口：RollingLeaderboard(*, timezone_name='UTC', max_entries=1000)；submit(*, player, score, achieved_at) 返回 SubmitOutcome；best_of(*, player, board='day', window)；top(*, board='day', window, limit=10)；roll_window(*, board='day', now)；purge_expired(*, now)；window_of(*, achieved_at, board='day')；history_best(player)；scores_of(player)；stats()；snapshot()；restore(state)。拒绝抛 RollingError(code, detail)。ScoreEntry 含 player、score、achieved_at、board、window、seq；SubmitOutcome 含 window、improved、best；WindowResult 含 board、window、winner、best_score、entries。

故障与边界：①窗口按指定时区的整点边界切分，日榜键为 YYYY-MM-DD，周榜键为 YYYY-Www；②提交同时写入日榜与周榜，窗口由事件时间决定而不是处理时间，没有有效时间戳的提交整条拒绝，不能落进当前窗口；③提交按（玩家、榜单、窗口）幂等，只保留最高分，同一玩家换窗口提交互不影响；④窗口内多次提交，低分不覆盖高分，improved 为假；⑤top 先按分数降序，再按达成时间升序，最后按序号升序；⑥top 的 limit 必须为正整数；⑦board 只接受 day 与 week，其它值一律拒绝；⑧roll_window 关闭当前时点之前的窗口，返回每个窗口的胜者、最高分与条数，然后清空这些窗口，已经关闭的窗口再次滚动时不再返回，也不能被重新写入；⑨purge_expired 同时清理两个榜单的过期条目，返回删除条数，重复调用返回 0；⑩每个榜单的存活条目受 max_entries 限制，超限拒绝新条目；⑪快照含 version、entries、history、stats、next_seq 五个键，深拷贝且与内部状态不共享结构；⑫恢复非法结构抛 ROLLING_SNAPSHOT_INVALID 且原状态不变。

硬约束：切窗瞬间到达的成绩必须归入新窗口，不能写进正在结算的旧窗口；过期剔除只影响榜单可见性，玩家历史最佳记录仍要保留；同一玩家同窗口多次提交只保留最佳，平分时保留更早达成的那条。

交付要求：实现放在模块文件内，公开签名与 dataclass 字段名保持不变；时间解析与时区换算自行处理；窗口键必须能直接比较先后，跨年也不能乱序；切窗和清理必须可重复执行且结果一致。

验收：python3 -m pytest -q tests 全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: rolling_leaderboard
- typeName: RollingLeaderboard
