# batch49-py-game-a-14

## 题面

运营后台对账时发现玩家金币的来源和回收对不上：任务奖励刷了几次就凭空消失，修理费扣到一半卡住，日结跑两遍流水直接翻倍。现在的账本把来源和回收记在同一张流水里，既没有日配额也没有挂账，出问题时只能按时间倒查。财务要求把铸币与回收按账号、分类、自然日管起来，涉及 gsb/batch49-py-game-a-14 下的 economy_flow_governor.py，类型 EconomyFlowGovernor。它给每类来源设每日铸币配额，给每类回收设每日回收配额，日结时统一结算并保留可审计的累计值。

公开接口：EconomyFlowGovernor(*, source_quotas=None, sink_quotas=None, carry_cap=500)；record_source(account, category, amount, day)；record_sink(account, category, amount, day)；quota_for(account, category, day)；settle_day(day)；balance(account)；audit(account)；configure_category(category, *, kind, daily_quota, carry_cap=None)；pending_for(account, category)；snapshot()；restore(snapshot)；current_day()。错误统一抛 FlowError(code, detail)。

默认来源配额 quest=1000、drop=500、tax_refund=200；默认回收配额 repair=300、fee=100、shop=800，构造参数可按分类覆盖。

故障与边界：
① 来源与回收分类分开登记；未知分类抛 FLOW_UNKNOWN_CATEGORY；account 为空、amount 非正整数、day 为空、kind 不是 source 或 sink 一律抛 FLOW_INVALID_REQUEST。
② record_source 在当日剩余配额内全额入账，返回 accepted、pending、balance、quota_left；额度用尽后的超出部分进入挂账队列而不是丢弃，挂账不影响当前余额。
③ 挂账按发生顺序保存，次日结算释放配额后按原顺序补发；补发只增加余额，不得回写成当日流水或重复计入 minted。
④ record_sink 同时受当日回收配额与余额约束，可扣部分按实际扣减计入 accepted，剩余计入 rejected，余额不得为负；回收配额与余额谁先耗尽都只扣到边界，回收只减余额，不影响来源配额。
⑤ 回收配额当日未用完可结转次日，但结转量有 carry_cap 上限；超出部分清零，不得滚入下下日。
⑥ quota_for 返回指定账号、分类、自然日的剩余额度；已结算日之后仍可按该日查询历史剩余，未知分类抛 FLOW_UNKNOWN_CATEGORY。
⑦ settle_day(day) 结算早于或等于最后结算日的日期返回 settled=False、released=0 且不产生副作用；新日期即使当天没有任何流水也返回 settled=True、day 和 released 数量。
⑧ 结算后该日及更早日期再记账抛 FLOW_DAY_CLOSED；未来日期不受影响；settle 之后 current_day 返回最后结算日。
⑨ 结算按账号汇总，同一天重复调用幂等；账号之间不得串账，一个账号的余额与挂账不得影响另一个账号。
⑩ 交易税返还默认算来源；通过 configure_category 把它切成负回收后，已记录流水保持原分类不重算，只影响切换后的新流水；daily_quota 必须为非负整数。
⑪ audit 返回 minted、sunk、pending 三个累计值；balance 对未知账号返回 0；这些查询不得修改状态，也不得触发补发。
⑫ snapshot 深拷贝余额、配额用量、挂账、分类配置与结算日；restore 结构非法抛 FLOW_SNAPSHOT_INVALID 且现场不变；所有写入口加锁，日结与记账不得交错。

验收：python3 -m pytest -q tests 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启恢复自行验证；只做本地实现，错误码与返回字段必须与上述名称一致。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: economy_flow_governor
- typeName: EconomyFlowGovernor
