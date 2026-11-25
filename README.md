# batch49-py-game-a-26

## 题面

匹配服最近连续报出同一位对手被反复配到一起。玩家在对手资料页点了“不再遇到”，下一轮匹配仍然撞上；换一位玩家点过之后，对面那边也没有生效。更麻烦的是长等待队列里的玩家明明已经排了很久，系统既不放宽也不解释为什么没配上，运营只能手工改名单救场。范围是 gsb/batch49-py-game-a-26 下的 match_avoid_registry.py，类 MatchAvoidRegistry，负责避让关系记账与候选过滤，匹配器每轮把当前队列整批交给它筛一遍。

公开接口：MatchAvoidRegistry(*, max_avoid_per_player=32, default_ttl=1800, relax_thresholds=(60, 180, 300))；add_avoid(owner, target, *, ttl=None, now=None)；remove_avoid(owner, target, *, now=None)；has_avoid(left, right)；avoid_pairs(player=None)；filter_candidates(ticket, candidates, *, now=None)；relax_level(wait_seconds)；expire(now=None)；snapshot()；restore(state)；stats()。时间戳统一为整数秒。拒绝时抛 MatchAvoidError(code, detail)，码为 MATCH_AVOID_INVALID_REQUEST 或 MATCH_AVOID_STATE_INVALID。MatchTicket 含 ticket_id、player、enqueued_at；AvoidEdge 含 owner、target、created_at、expires_at；stats 有 added、refreshed、removed、expired、filtered、relaxed 六个计数，构造时全部为零。

需要处理的情况：① 同一对玩家重复添加只刷新创建时间与到期时间，不产生第二条，统计里记 refreshed，added 不再增加；② 自身避让、空串、纯空白、非字符串、ttl 非正数一律拒绝，且不留下任何记录、不改动统计；③ 删除不存在的避让返回 False，删除成功返回 True 并计入 removed，重复删除保持幂等；④ 到期时间小于等于当前时刻的关系视为失效，filter 与 has_avoid 都不得再据它排除；⑤ expire 只清出已到期的关系并返回条数，重复调用返回 0，到期时间刚好等于当前时刻也算到期，已失效的关系不必等 expire 就能被 filter 忽视，expire 只是把账清干净；⑥ filter 先剔除自己与重复 ticket_id（保留首次出现的那条），再剔除与当前玩家存在有效避让的候选，被剔除的条数累计进 filtered；⑦ filter 结果按等待时长从长到短排序，等待相同按 ticket_id 升序，同样的输入必须永远得到同样的顺序；⑧ relax_level 按阈值元组返回层级，未达到第一个阈值为 0 层，达到第 k 个阈值即 k 层，超过最后一个阈值仍为最高层，负等待直接拒绝；⑨ 等待达到阈值后按层级放宽最老的若干条避让关系，不是一次性清空全部，被放宽的条数累计进 relaxed；⑩ 每名玩家的避让名单有容量上限，超限时淘汰最早到期的一条，不得拒绝新增；⑪ 快照为深拷贝，恢复时结构不合法抛 MATCH_AVOID_STATE_INVALID 且现有状态不动。

硬约束：同一玩家并发添加多条避让时不得互相覆盖，条数与统计必须准确；快照恢复后关系、统计与到期时间都要还原，并且快照与内部结构不共享引用；避让名单容量为硬上限，过滤判定在锁内一次完成。

验收：`python3 -m pytest -q tests` 共 15 条必须全过，不得改测试、跳过或用固定输入写死；并发、长等待放宽与重启恢复都要自己构造场景验证；只做本地实现，不引入第三方依赖。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: match_avoid_registry
- typeName: MatchAvoidRegistry
