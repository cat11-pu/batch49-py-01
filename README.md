# batch49-py-game-a-21

## 题面

国庆版本上线后，公会系统里同一个隐藏成就在不同角色上的表现完全对不上：有人三条线索都看完了，成就栏还是空的；同一个账号换小号重打同一段副本，线索进度又从零开始；还有玩家反馈成就达成过一次，延迟发放的奖励到账后又弹了一遍解锁提示，客服只能手工核对补发记录。范围限定在 gsb/batch49-py-game-a-21 的 hidden_achievement_hints.py，类型 HiddenAchievementHints，负责隐藏成就的线索分段、账号级进度与奖励发放。

公开接口：register_hidden(achievement_id, name, hints, *, reward_id="", reward_delay_seconds=0)、reveal_hint(account_id, achievement_id, stage, *, role_id="main")、progress_of(account_id, achievement_id)、unlock(account_id, achievement_id, *, role_id="main")、visible_for(account_id)、reissue_reward(account_id, achievement_id)、pending_rewards(account_id)、total_hints(achievement_id)、is_achieved(account_id, achievement_id)、stats(account_id)、snapshot()、restore(state)。拒绝时抛 AchievementError(code)；visible_for 返回 AchievementView 列表，字段为 achievement_id、name、total_hints、revealed_hints（元组）、achieved、reward_id。

要修的点：①成就 id 或名称为空、hints 为空或含空白串一律 ACH_INVALID，同 id 重复注册 ACH_DUPLICATE，注册数到 max_achievements 上限报 ACH_CAPACITY，已注册的照常可查；②reveal_hint 只有传当前期望段位才推进一段，返回 revealed、stage、progress，同一段位重复触发返回 revealed=False、reason="already"，hints_revealed 只计一次，progress_of 对从未参与过的账号返回 0；③段位为负或不小于总段数报 ACH_BAD_STAGE，未知成就报 ACH_UNKNOWN；④线索必须按段位顺序解锁，跳段触发只累加 stats 的 skipped、进度留在原段，不得把后面的段位补上；⑤同一账号下不同角色分别触发同一线索时整体只推进一段，进度按账号聚合，不能按角色叠加；⑥线索没集齐时 unlock 返回 unlocked=False、reward_id 为空串，状态与发奖计数不变；集齐后返回 unlocked=True、reward_state="granted"；⑦已解锁再调 unlock 返回 reward_state="already_unlocked"，成就计数与发奖计数都不再增加，空账号报 ACH_INVALID；⑧visible_for 不得把一条线索都没露出的隐藏成就暴露出去，已达成的一律可见，结果按 achievement_id 升序、revealed_hints 按段位升序，reward_id 在未解锁时也要原样带出，客户端靠它预加载图标；⑨reward_delay_seconds 大于 0 时 unlock 只登记待发，pending_rewards 按 id 升序，reissue_reward 把待发转已发，没有待发时返回 reissued=False、reason="nothing_pending"，补发只增加 rewards_granted，achieved 不得再加；⑩stats 固定返回 achieved、hints_revealed、rewards_granted、pending_rewards、skipped 五项，按账号隔离，陌生账号返回全零；⑪snapshot 只返回可比较的纯数据字典，顶层必须同时有 achievements 与 accounts，同状态两次快照相等，restore 结构缺键或类型不符报 ACH_SNAPSHOT_INVALID 且不破坏当前状态。

硬约束：reveal_hint 与 unlock 会被多路网关协程并发调用，同一账号同一成就的推进必须串行，并发下不得重复发奖；同一账号在多个网关重复提交同一段位必须幂等，既不能把段位推快，也不能把待发奖励翻倍；单账号待发奖励条数受 max_pending_rewards 硬顶，超限要报错不能静默丢弃；进程重启后凭 snapshot 与 restore 恢复，进度、已达成、待发与五项计数必须逐项对上。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，测试不许改、不许跳过、不许按固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: hidden_achievement_hints
- typeName: HiddenAchievementHints
