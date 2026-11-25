# batch49-py-game-a-25

## 题面

跨服世界频道在活动开启的三十秒里被同一名玩家刷了两百多条一模一样的招募广告，禁言名单上的账号还在连续发言，活动管理员想发公告时自己反而被限流挡住。范围是 gsb/batch49-py-game-a-25 的 chat_rate_governor.py，类型 ChatRateGovernor，负责频道令牌桶限流、重复内容折叠与禁言。

公开接口：allow(player_id, channel, *, at, is_admin=False)、record(player_id, channel, body, *, at)、fold_count(channel, player_id)、mute(player_id, *, until, reason="")、unmute(player_id)、is_muted(player_id, *, at)、state(player_id, channel)、set_capacity(value)、set_refill_rate(value)、reset_player(player_id)、snapshot()、restore(state)。拒绝时抛 GovernorError(code)；at、until 都是秒为单位的数值。

要修的点：①allow 按玩家与频道的组合各自独立建桶，桶初值为 capacity，按 at 与上次判定的时间差乘以 refill_per_second 补令牌并封顶在 capacity，够 1 个才放行并扣 1，返回 allowed、reason（ok、rate_limited、muted）、tokens（保留 3 位小数）；②令牌桶按频道独立计算，跨频道不共享，不同玩家之间也不共享；③禁言期内的消息直接拒绝，reason="muted"，并且不扣令牌，桶保持原有令牌数；④禁言刚好结束那一刻到达的消息按恢复后的令牌桶判定，不得把禁言期间的令牌补扣回去；⑤mute 的 until 必须大于 0 否则 GOV_BAD_UNTIL，重复禁言以新的 until 为准，禁言按账号维度生效、跨频道共享，unmute 返回之前是否处于禁言，is_muted 在 at 小于 until 时为 True；⑥record 只做折叠记账、不消耗令牌，body 为空报 GOV_INVALID，返回 folded 与 count，禁言不影响折叠记账；⑦同一玩家在同一频道连续发送相同内容、相邻两条间隔不超过 fold_window_seconds 时折叠为一条并累计次数，folded=True，同一条消息在折叠组里只展示一次，count 表示含首条在内的累计条数；⑧折叠只对窗口内连续相同内容生效，中间插入不同内容即打断折叠，之后相同内容重新独立展示；⑨fold_count 返回当前折叠组的累计次数，没有记录或已经超出窗口返回 0，state 对从未出现过的桶返回满令牌、fold 为 0；⑩管理员与普通玩家使用独立桶，管理员发言不占普通玩家的桶额度；⑪state 返回 tokens、capacity、fold 三项，set_capacity 只接受 1 到 100 否则 GOV_BAD_CAPACITY，set_refill_rate 接受 0 到 100 否则 GOV_BAD_RATE，reset_player 清掉该玩家所有桶并返回清掉的条数，单实例跟踪的桶数受 max_tracked 硬顶、超限按最久未使用淘汰。

硬约束：allow 与 record 会被多个网关协程并发调用，同一个桶的补令牌与扣令牌必须串行，并发下不得超发，也不能出现负数令牌；令牌桶、折叠组与禁言名单都受 max_tracked 硬顶，超限淘汰最久未使用的条目而不是无限增长；进程重启后凭 snapshot 与 restore 恢复，各桶令牌、上次判定时间、折叠组与禁言到期必须逐项对上，快照顶层有 buckets 与 mutes 两键，结构不符报 GOV_SNAPSHOT_INVALID 且不破坏当前状态。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，测试不许改、不许跳过、不许按固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: chat_rate_governor
- typeName: ChatRateGovernor
