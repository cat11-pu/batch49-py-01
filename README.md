# batch49-py-game-a-17

## 题面

游戏交易行客服转来一组跨服纠纷：同一笔报价被两边各点了一次成交，物品却只从一方扣走；改价后的旧报价仍能成交；谈崩后卖家的物品一直显示已预占；维护重启后整场协商记录消失。更离奇的是，两边都坚称自己点过接受，后台却只记了一方的动作。值班同学手工补单时还发现，有人连续改价三次把对手绕晕，最后按最早那版结了账；也有人接受报价后立刻取消，系统仍然扣了物品。范围是 gsb/batch49-py-game-a-17 的 trade_offer_negotiation.py，类 TradeOfferNegotiation，用 OfferVersion 与 TradeResult 两个 dataclass 承载报价版本和接受结果。

公开接口：TradeOfferNegotiation(*, offer_ttl=300, max_versions=20)；open_session(*, session_id, player_a, player_b, now)；set_inventory(player, items)；inventory_of(player)；create_offer(*, session_id, proposer, give, want, now) 返回 OfferVersion；counter_offer(*, session_id, proposer, give, want, now)；accept(*, session_id, player, version, now) 返回 TradeResult；reject(*, session_id, player, version=None, now=None)；cancel(*, session_id, player, now=None)；state(*, session_id, now=None)；versions_of(session_id)；snapshot()；restore(state)；stats()。拒绝抛 TradeError(code, detail)。OfferVersion 含 version、proposer、give、want、created_at、expires_at 六个字段，TradeResult 含 status、version 两个字段。

故障与边界：①会话号唯一、双方必须是两个不同的非空玩家，会话创建后参与者不可更改，同一会话号重复创建要报已存在错误；②物品清单只接受正整数，创建报价时 give 与 want 都不得为空，物品名按字典序归一；③报价存成不可变版本记录，版本从 1 开始，创建时间与过期时间都要落库；④每次还价生成新版本，旧版本立即失效，版本号在同一会话内单调递增；⑤接受、拒绝、取消只能由会话内玩家发起，外部玩家一律拒绝；⑥接受当前版本后状态为 pending，双方都在同一版本上接受才成交；⑦同一方重复接受同一版本保持 pending，不重复交换，也不重复计数；⑧成交时双向扣加物品，数量归零的条目从库存移除，两边库存要么全变要么全不变；⑨报价超过 offer_ttl 秒失效，之后的状态为 expired，过期报价不能复活，过期时间按创建时间加有效期计算，不受后续操作影响；⑩拒绝与取消会关闭会话，关闭后不能再接受或还价，重复关闭保持幂等；⑪版本数达到 max_versions 后不得继续还价，已有版本仍可接受；⑫快照包含 version、sessions、inventories、stats 四个键，深拷贝且可恢复；恢复非法结构抛 TRADE_SNAPSHOT_INVALID 且原状态不变。

硬约束：还价必须双向轮换，同一方连续还价两次要被拒绝；接受旧版本返回版本冲突，不得偷偷按最新版本成交；成交瞬间任一方物品已被占用时整单失败，另一方已经建立的预占必须释放。

交付要求：实现只改上述模块，接口签名保持不变；版本记录与接受结果使用 dataclass，内部索引自行设计；恢复失败后原会话必须还能继续使用。

验收：python3 -m pytest -q tests 全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: trade_offer_negotiation
- typeName: TradeOfferNegotiation
