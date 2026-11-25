# batch49-py-game-a-13

## 题面

主城的杂货商这周报价乱跳：同一件货连续问两次价格不一样，刚卖出一批货价格反而往上蹿，日切后又不回基准价，买卖两边偶尔还会倒挂。更离谱的是大额买单敲进去之后，价格能在几秒内被后续查询抬到离谱的位置，摆摊的玩家开始利用这个空子刷差价。数值组查下来是价格索引没有统一的中间价与结算口径，涉及 gsb/batch49-py-game-a-13 下的 dynamic_price_index.py，类型 DynamicPriceIndex。它按成交压力、供需弹性、日切回落和冲击衰减维护每件商品的报价。

公开接口：DynamicPriceIndex(*, decay_bp=2500, spread_bp=20, min_spread=1, max_impact_units=1000)；register_item(item_id, base_price, stock)；set_base_price(item_id, base_price)；set_elasticity(item_id, elasticity)；quote(item_id)；record_trade(item_id, side, qty)；daily_settle(day)；price_history(item_id)；stock_of(item_id)；pressure_of(item_id)；snapshot()；restore(snapshot)。错误统一抛 PriceError(code, detail)。

故障与边界：
① 商品重复注册抛 PRICE_DUPLICATE_ITEM；base_price 小于 1、stock 为负、item_id 为空串抛 PRICE_INVALID_REQUEST。
② 中间价按 mid = max(1, base_price + delta) 计算，delta 由 pressure 与 elasticity 得出：pressure 为正时取 pressure * elasticity // 100，为负时取负的绝对值乘弹性整除 100；默认 elasticity=10，pressure 是所有历史成交的净和，直到日切衰减。
③ 价差 spread = max(min_spread, mid * spread_bp // 10000)，bid = mid - spread // 2，ask = bid + spread；任何报价都满足 ask - bid >= min_spread 且 bid >= 1。
④ quote 不做缓存也不改状态，同一时刻没有新成交时连续报价完全一致；返回 bid、ask、mid、spread、stock 五个字段。
⑤ record_trade 的 side 只接受 buy 与 sell，qty 必须是正整数，否则抛 PRICE_INVALID_REQUEST；buy 让 stock 减 qty、pressure 增加有效冲击，sell 让 stock 加 qty、pressure 减少有效冲击，成交数量与计入压力的数量分开记账，返回 item、side、qty、stock、pressure、mid。
⑥ buy 数量超过库存抛 PRICE_INSUFFICIENT_STOCK，库存、压力和历史都不动；单笔计入压力的数量有 max_impact_units 上限，超出部分只改库存不改压力。
⑦ 未知商品在 set_base_price、set_elasticity、quote、record_trade、price_history、stock_of、pressure_of 上统一抛 PRICE_UNKNOWN_ITEM，这些查询对未知商品返回错误而不是零值。
⑧ set_elasticity 接受非负整数，0 表示价格完全不随压力变化；负数抛 PRICE_INVALID_REQUEST。set_base_price 必须为正，只改基准不改库存与压力。
⑨ daily_settle(day) 对每个商品把 pressure 按 decay_bp 衰减，正负都按绝对值计算后保留符号；把衰减后的中间价追加进该商品历史，返回 settled=True、items 数量与 prices 映射，prices 只包含结算成功的商品，按 item_id 排序返回。
⑩ 同一天重复 daily_settle 幂等：返回 settled=False，不衰减、不追加历史；传入早于已结算日的日期同样不生效，只把最后结算日推进到更晚的日期。
⑪ 日切后的价格以基准价为锚做钳制，连续多日累计漂移不得突破基准价的上下百分之三十；钳制只发生在日切，不随 quote 次数或查询次数变化，钳制后的价格仍要满足最小价差与 bid >= 1。
⑫ snapshot 深拷贝基准价、库存、压力、弹性、历史与最后结算日；restore 结构非法抛 PRICE_SNAPSHOT_INVALID 且现场不变；写入口加锁，quote 与 record_trade 并发时不得读到半更新的库存或压力，历史只追加不可变。

验收：python3 -m pytest -q tests 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启恢复自行验证；只做本地实现，错误码与返回字段必须与上述名称一致。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: dynamic_price_index
- typeName: DynamicPriceIndex
