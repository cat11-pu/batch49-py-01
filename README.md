# batch49-py-game-a-23

## 题面

赛季结算发奖励那晚，运营发现公会里几个连续两周没打过团本的成员照样领着满额职级奖励，另一些被误判降级的成员在申诉通道里提交了单据却始终没有恢复，跨周补录的团本贡献还总是算到错误的那一周上。范围是 gsb/batch49-py-game-a-23 的 guild_activity_review.py，类型 GuildActivityReview，负责按周统计贡献、观察期、自动降级与申诉恢复。

公开接口：join_member(member_id, rank_level, *, week)、record_contribution(member_id, amount, *, occurred_at)、weekly_review(week)、status(member_id)、observe(member_id)、appeal(member_id, *, at)、restore_rank(member_id)、rank_of(member_id)、week_of(occurred_at)、history(member_id)、set_threshold(value)、snapshot()、restore(state)。拒绝时抛 ReviewError(code)；职级为 1 到 max_rank 的整数。

要修的点：①join_member 职级必须是 1 到 max_rank 的整数，重复加入报 REVIEW_DUPLICATE，职级越界报 REVIEW_BAD_RANK，成员数到 max_members 硬顶报 REVIEW_CAPACITY，成功返回 member、rank、week；②week_of 按公会本地时区（默认 UTC+8）切 ISO 周，返回 YYYY-Www 形式的字符串；③record_contribution 的 amount 必须为正整数否则 REVIEW_BAD_AMOUNT，未知成员报 REVIEW_NO_MEMBER，返回 member、week、total，total 是该成员该周的累计值；④跨周补录的贡献要回填到发生周，不能计入当前周的考核；⑤weekly_review 只考核该周之前已加入且未被移出的成员，返回 week、reviewed、probation、demoted、removed 五个字段且三个名单按成员 id 升序：该周累计达到阈值记 active 并把连续不达标次数清零，低于阈值则次数加一，第一次进 probation，第二次降一级并记下降级前职级，降级后状态仍为 probation；⑥已经是最低职级的成员连续两周不达标直接移出，status 返回 removed、rank_of 返回 0，之后不再参与考核；⑦同一周重复考核幂等，第二次调用返回与第一次完全相同的结果，不得再降一级，也不得把已经恢复的成员重新降级；⑧appeal 必须在降级当周或下一周内提出，超期报 REVIEW_APPEAL_WINDOW，成功返回 accepted=True 与 previous_rank 并把状态转成 appealed；申诉期内的成员不参与下一轮自动降级，但其贡献仍照常统计；⑨restore_rank 只对已申诉成员生效，否则报 REVIEW_NOT_APPEALED，成功把职级恢复到降级前职级、状态回到 active、连续不达标次数清零，返回 member 与 rank；⑩恢复原职级时若原职级已不存在，落到最接近的可用职级，不得把成员恢复到已删除的职级；⑪observe 返回 member、status、missed；history 按周升序返回该成员历次考核记录；set_threshold 负数报 REVIEW_BAD_AMOUNT 且只影响之后的考核；snapshot 顶层同时有 members 与 weeks 两键，同状态两次快照相等，restore 结构不符报 REVIEW_SNAPSHOT_INVALID 且不破坏当前状态。

硬约束：weekly_review 与 record_contribution 会被多个公会服务协程并发调用，同一成员的周累计与连续不达标次数必须串行推进，不能出现半次降级；成员总数受 max_members 硬顶，超限加人报错而不是静默丢弃；进程重启后凭 snapshot 与 restore 恢复，职级、状态、连续不达标次数、各周累计与已考核周必须逐项对上。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，测试不许改、不许跳过、不许按固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: guild_activity_review
- typeName: GuildActivityReview
