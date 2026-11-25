# batch49-py-game-a-05

## 题面

新章节上线后，任务链进度出现两类怪事：走左边分支的玩家把目标刷到超额，右侧分支还没动，汇合节点的奖励却已经到手；另一些玩家从两条路径都经过同一个共享节点，同一件道具被发了两次，补发记录里同一行出现两遍。范围锁定 gsb/batch49-py-game-a-05 的 quest_branch_convergence.py，类型 QuestBranchConvergence，把章节任务链按分支独立记账、汇合门禁和共享节点一次性结算。

章节任务链是有向无环图，几条并行分支各自累计目标进度，汇合节点要等名单里的分支全部完成才解锁；同一节点被多条路径到达时奖励只发一次，但要记住被到达过几次。客户端会重发进度事件，服务端按事件编号去重，重放不能重复计数，也不能把已经发过的奖励再发一遍。客服还需要能按链导出结算明细，哪一步发奖、哪一步只记到达次数都要能对上。

公开接口：QuestBranchConvergence(*, max_branches=8)；start_chain(chain_id, *, branches, targets, rewards=None, merges=None)；advance_branch(chain_id, branch_id, node_id, delta, event_id)；check_merge(chain_id, merge_id)；unlock_merge(chain_id, merge_id)；progress(chain_id)；node_state(chain_id, node_id)；reward_total(chain_id)；abandon_chain(chain_id)；resume_chain(chain_id)；snapshot()、restore(state)、stats()。advance_branch 返回含 progress、completed、granted 的字典，progress 返回含 chain_id、abandoned、reward_total、branches、merges 的纯字典，每个分支节点给出 progress、target、completed 三项，node_state 返回的 NodeProgress 含 node_id、progress、target、completed、reach_count、reward_granted。拒绝一律抛 QuestError(code, detail)。

要查清的现象与边界：① start_chain 的分支表非空且每个节点在 targets 里有正整数目标，汇合节点引用的分支必须存在，重复链报 QUEST_DUPLICATE_CHAIN，结构非法报 QUEST_INVALID_CHAIN；② 同一分支内节点必须按顺序推进，跳过前置节点报 QUEST_OUT_OF_ORDER；③ 未知链、分支、节点、汇合节点分别报 QUEST_UNKNOWN_CHAIN、QUEST_UNKNOWN_BRANCH、QUEST_UNKNOWN_NODE、QUEST_UNKNOWN_MERGE，node_id 不属于该分支也算未知节点；④ 进度按（链，分支，节点）记账，delta 必须为正，否则报 QUEST_INVALID_DELTA；⑤ 事件编号全局唯一，重放同一编号直接返回首次结果，不重复计数也不重复发奖，去重次数进 stats；⑥ 节点进度封顶在目标值，首次达成时发一次奖励，reward_total 累加；⑦ 汇合节点在全部前置分支完成前保持锁定，unlock_merge 报 QUEST_MERGE_LOCKED，check_merge 返回假；⑧ 前置满足后解锁，只发一次奖励并返回奖励数额，重复解锁报 QUEST_MERGE_DUPLICATE；⑨ 共享节点从两条路径到达时不得重复发奖，但 reach_count 要累加；⑩ 放弃链保留已完成的一次性节点状态，放弃期间推进报 QUEST_ABANDONED，放弃后重开同一链，已完成的一次性节点保持完成态且不重新发奖；⑪ progress 返回纯字典且是深拷贝，改动返回值不影响内部状态，结构含 branches、merges、abandoned、reward_total；⑫ 快照恢复后进度、去重事件、已发奖励与解锁状态都在，结构不全报 QUEST_INVALID_STATE 且原状态不变，stats 返回 chains、grants、deduped、merges、abandoned 五项。

硬约束：进度写入按事件编号幂等，同一事件重放多少次都只生效一次；同一链的并发写入必须串行，几条分支同时推进不能互相覆盖进度；重启后从快照恢复，去重集合、已发奖励与汇合解锁状态必须一起回来，不能出现奖励重发的窗口。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: quest_branch_convergence
- typeName: QuestBranchConvergence
