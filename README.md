# batch49-py-game-a-24

## 题面

客服复盘一起辱骂事件时发现，世界频道里已经撤回的消息在客户端还能拉到正文，中间还断了一截序号；管理员导出的历史版本和玩家截图对不起来；两个管理员同时改同一条公告时，后提交的直接盖掉前一个，谁改过什么完全查不出。范围是 gsb/batch49-py-game-a-24 的 chat_recall_journal.py，类型 ChatRecallJournal，负责撤回墓碑、编辑版本链与频道序号。

公开接口：post(channel, author, body, *, at)、edit(channel, seq, author, body, *, expected_version, at)、recall(channel, seq, actor, *, at)、history(channel, seq, *, role="member")、fetch_range(channel, start, end)、seq_of(channel)、latest(channel, seq)、channels()、audit_entries()、set_version_limit(n)、snapshot()、restore(state)。拒绝时抛 ChatError(code)；消息与墓碑分别用 ChatMessage、Tombstone 两个 dataclass 表示。

要修的点：①post 的频道、作者、正文都必须非空否则 CHAT_INVALID，正文长度超过 body_limit 报 CHAT_BODY_TOO_LONG，成功返回 ChatMessage（seq、channel、author、body、version、posted_at、edited_at，新消息 version 为 1、edited_at 为空），seq 按频道从 1 起单调递增；②seq_of 返回该频道最后分配的序号，从未用过的频道返回 0，channels() 按首次出现顺序返回；③edit 的 expected_version 与当前版本不等必须报 CHAT_VERSION_CONFLICT 且当前版本保持不变，作者不符报 CHAT_FORBIDDEN，消息不存在报 CHAT_NOT_FOUND，成功时版本加一、edited_at 置为传入的 at；④编辑版本数超过上限时必须拒绝并保留最后一版，不能静默丢弃已经成功的那一版，拒绝时不改 edited_at；⑤recall 只有原作者能撤否则 CHAT_FORBIDDEN，距发布超过 recall_window_seconds 报 CHAT_RECALL_WINDOW，时限从 posted_at 起算、edited_at 不改变时限，已撤回再撤报 CHAT_RECALLED，成功返回 Tombstone（seq、channel、author、recalled_at、recalled_by）；⑥撤回后的墓碑仍占用原序号，后续 post 的序号不得回填，同一频道内序号连续、不因撤回出现空洞，区间拉取时以 tombstone 形式返回、正文为空；⑦fetch_range 起止非法（start 小于 1 或 end 小于 start）报 CHAT_INVALID，end 超过当前序号时按现有消息返回、不报错，结果按 seq 升序，每条含 seq、kind、body、version；⑧history 只有 role="admin" 可调用，否则 CHAT_FORBIDDEN，返回第 1 版到当前版本的列表、每项含 version、body 与 edited_at，管理员查看历史版本要单独记一条审计，普通拉取只返回当前版本；⑨latest 返回当前版本的 seq、kind、body、version，未知序号报 CHAT_NOT_FOUND；⑩audit_entries 按追加顺序返回，每条含 seq（从 1 起）与 action，返回副本、外部修改不影响内部；⑪set_version_limit 只接受正整数否则 CHAT_INVALID，调整只影响之后的编辑，正文长度与版本数量都是硬顶；⑫snapshot 顶层同时有 channels 与 limits 两键，同状态两次快照相等，restore 结构不符报 CHAT_SNAPSHOT_INVALID 且不破坏当前状态。

硬约束：同一消息的并发编辑必须按版本号乐观锁串行提交，同一 expected_version 只有一路能成功、其余一律 CHAT_VERSION_CONFLICT，频道序号在并发 post 下不得重号；单频道正文长度与版本数量都是硬上限，超限拒绝而不是截断或覆盖；进程重启后凭 snapshot 与 restore 恢复，各频道序号、当前版本、历史版本、墓碑与审计必须逐项对上。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，测试不许改、不许跳过、不许按固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: chat_recall_journal
- typeName: ChatRecallJournal
