# batch49-py-game-a-18

## 题面

赛季奖励公告贴出后，公会群直接炸锅：两名同分玩家一个拿金一个拿银，百分比分段偶尔算出 0 个名额导致后面整段奖励错位，补发邮件在结算后又被新提交改写，服务器重启后榜单和已发奖励一起消失。更麻烦的是，有玩家拿着结算截图来对账，后台按当前榜单重算出来的奖励和截图完全对不上，说明发奖没有真正冻结在当时的名次上。运营先按人数重算了一遍分段，发现空段被后面的段吞掉，奖励名单比公告少了整整一档。范围是 gsb/batch49-py-game-a-18 的 leaderboard_tie_bands.py，类 LeaderboardTieBands，按赛季维护成绩、名次、奖励分段与结算快照。

公开接口：LeaderboardTieBands(*, bands=None)；submit(*, season, player, score, achieved_at) 返回 SubmitResult；rank_of(*, season, player)；top(*, season, limit)；bands(*, season)；recompute_bands(*, season, ratios)；settle(*, season, now=None) 返回 Settlement；settlement(settlement_id)；rewards(settlement_id)；score_of(*, season, player)；count(season)；stats()；snapshot()；restore(state)。拒绝抛 LeaderboardError(code, detail)。Entry 含 player、score、achieved_at、seq；Band 含 name、min_rank、max_rank、reward；SubmitResult 含 accepted、improved、best_score、rank；Settlement 含 settlement_id、season、created_at、entries、bands。

故障与边界：①提交按（赛季、玩家）幂等，只保留最高分，重复提交不新增记录；②低分提交仍被接收，但不覆盖最佳成绩，improved 为假，排名也不回退；③同分时保留更早达成的记录，后到的同分提交不改变达成时间与序号；④排序先按分数降序，再按达成时间升序，最后按序号升序，展示顺序必须稳定；⑤密集排名：同分玩家名次相同，下一档名次只加一，不允许跳号，名次只由最高分决定，与提交先后无关；⑥top 的 limit 必须为正整数，超出人数时返回全部；⑦默认分段为 S/A/B/C，累计比例 0.1、0.3、0.6、1.0，按参赛人数向下取整算边界，最后一段兜住余数；⑧recompute_bands 接受（名称、累计比例、奖励）列表，比例必须严格递增且最后一项为 1.0，非法比例整单拒绝；⑨settle 冻结当时的成绩与分段，rewards 按冻结名次发奖，同一结算编号只生成一次；⑩settlement 与 rewards 查询不存在的编号要报错，不能返回空结构；⑪快照含 version、seasons、settlements、ratios、stats、next_settlement_id 六个键，深拷贝且与内部状态不共享结构；⑫恢复非法结构抛 LEADERBOARD_SNAPSHOT_INVALID，原有赛季、结算与统计全部保留。

硬约束：同分玩家密集排名相同，但展示顺序按达成时间，二者不能混用；分段边界按百分比算出的零名额段要合并到相邻段，不能出现空段；结算快照生成后新提交不影响本次奖励，只影响下次结算。

交付要求：实现放在模块文件内，公开签名与 dataclass 字段名保持不变；分段边界、名次与冻结快照必须互相对齐；同一赛季可以多次结算，编号单调递增；恢复失败时旧数据不能丢。

验收：python3 -m pytest -q tests 全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: leaderboard_tie_bands
- typeName: LeaderboardTieBands
