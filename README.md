# batch49-py-game-c-05

## 题面

外语直播的字幕条往前蹦两行，又忽然改掉念过的半句；同一段话重复推两次，修订计数翻倍；导播改个词，字幕又跳回旧版。事故出在一场跨国发布会，字幕先显示上一句的结尾再跳回，观众截图投诉；同传同事按字幕对稿，结果念错公司名；回放平台把重复修订的字幕导出成两条，后期只能逐条手改。字幕是这场发布会唯一的中文出口，错一句就是一次播出事故；引擎每隔一小段时间就推一次全量假设，重复与回改本来就该在这一层被吃掉，而不是留给下游去猜。范围是 gsb/batch49-py-game-c-05 的 live_caption_reviser.py，类 LiveCaptionReviser，把识别引擎的增量假设按稳定窗口提交，已提交部分不可回改，未稳定的尾巴允许继续修订。

公开接口：LiveCaptionReviser(window_ms, max_pending_chars, max_revisions, max_streams)、push_hypothesis(stream_id, text, now_ms)、stabilize(stream_id, now_ms)、commit(stream_id)、revise、window、latency、text、streams、snapshot、restore、stats；拒绝抛 CaptionError(code, detail)，错误码与 stats 键取模块顶部 ERROR_CODES、STATS_KEYS。

边界：①流标识为空或非串、文本非串拒；路数超 max_streams 拒；②同一路只留一份待提交尾部，新假设整体替换而不是追加；③与已提交前缀一致时，待提交只是去掉前缀的尾巴；④比已提交前缀短且不一致时，已提交不回退，只从分歧点后重新开一段；⑤同一时间片重复且内容相同的假设按幂等处理，不重复计修订也不刷新窗口；⑥待提交字符超 max_pending_chars 拒，修订超 max_revisions 拒；⑦stabilize 窗口未到返回 waiting 与 remaining_ms，到点提交尾部；⑧窗口到期但引擎没有新假设时按原样提交当前尾部，不得无限等待；⑨commit 强制提交当前尾部，提交后不允许再改；⑩revise 只改待提交尾部，碰到已提交报错；latency 返回到上次假设的毫秒数；⑪text 返回 committed、pending、display，streams 按字典序，stats 按键计数。

补充口径：committed 一旦提交不可回退，pending 只是 committed 之后的尾巴，display 在两者都非空时用一个空格连接，只有一个非空时直接给出该段；stabilize 的窗口从最后一次被接受的假设起算，重复假设既不刷新窗口也不增加修订；revisions 统计被接受的假设与成功 revise 的次数，拒绝的调用不计入；text、streams、stats 返回副本，读取时不得看到半个提交。

硬约束：多路各自独立，一路提交不影响另一路；提交与显示读取并发安全，读到的 committed 与 pending 同属一个时刻，不允许新 committed 配旧 pending；同一路的待提交尾部只保留最新一份，旧尾部整体替换而不是追加。snapshot 脱离活对象，restore 后提交态、待提交、窗口起点与计数都在，非法快照拒；上限按调用前的状态判定，超限的调用不留半截状态。

验收：`python3 -m pytest -q tests` 15 条全过；不改测试、不跳过、不写死；并发与重启自验；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: live_caption_reviser
- typeName: LiveCaptionReviser
