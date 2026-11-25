# batch49-py-game-a-27

## 题面

抽卡活动上线第二周，客服收到一类奇怪的账：玩家攒够天井兑换券换走了当期角色，普通保底计数被顺手清空，下一次保底从小保底重新数；另一个玩家在角色池攒的券，切到武器池后凭空少了一半；活动结束后没花完的券又整批消失，而上一期的券其实还能用；活动页明明写着“兑换不影响保底”，对账时却对不上。范围是 gsb/batch49-py-game-a-27 下的 spark_exchange_vault.py，类 SparkExchangeVault，负责兑换券记账、兑换与卡池结转。

公开接口：SparkExchangeVault(*, catalog=None, spark_ttl=604800, pity_threshold=90)；register_pool(pool_id, pool_type)；pull(pool_id, *, count=1, now=None)；sparks_of(pool_type, *, now=None)；pity_count(pool_type)；quote(request_id, pool_id, item_id, *, now=None)；exchange(request_id, pool_id, item_id, *, now=None)；carry_over(source_pool_id, target_pool_id, *, now=None)；expire(now=None)；snapshot()；restore(state)；stats()。catalog 形如 {"character": {"sword": 3}}，券以批次记账，每批带卡池、类型、张数与到期时间。时间戳为整数秒。错误抛 SparkError(code, detail)：SPARK_INVALID_REQUEST、SPARK_UNKNOWN_POOL、SPARK_POOL_CONFLICT、SPARK_UNKNOWN_ITEM、SPARK_INSUFFICIENT_SPARKS、SPARK_POOL_TYPE_MISMATCH、SPARK_STATE_INVALID。stats 有 pulls、sparks_granted、sparks_spent、sparks_expired、exchanges、duplicates、carried 七个计数，构造时全部为零。

需要处理的情况：① 每次抽取给所属卡池类型记 1 张券，券带独立到期时间，单次抽取数量限定 1 到 50 的整数；② 保底计数按卡池类型累计，达到阈值的那一手结果为 featured 并把计数清零，其余为 normal；③ 兑换券按类型分别记账，sparks_of 只统计该类型未过期的部分，传 now 时按该时刻判断过期但不改动存储；④ quote 只计算费用、当前券数与是否够兑，不改动任何状态也不计入统计；⑤ exchange 依次校验卡池、物品、请求号、余额，通过后从最早到期的券开始扣减，返回回执含 cost、sparks_after 与状态 ok；⑥ 兑换不改动普通保底计数，保底只跟抽取次数走，与兑换流水无关；⑦ 同一 request_id 只结算一次，重复提交返回首次结果、不重复扣券；⑧ carry_over 只允许同一类型卡池之间结转，返回搬走的券数，类型不同直接抛 SPARK_POOL_TYPE_MISMATCH，不做任何折算；⑨ 结转前先把已过期的券清掉再搬，过期部分不跟着走；⑩ expire 清出过期券并返回张数，重复调用返回 0，刚好到期也计入过期，过期券不参与余额也不被回执引用；⑪ 同一卡池重复登记为同一类型时保持幂等，不重置保底计数也不清券；⑫ 快照深拷贝，恢复时结构不合法抛 SPARK_STATE_INVALID 且现有状态不动。

硬约束：并发兑换同一账号时不得超扣，扣减与回执写入必须原子完成，余额与统计必须一致；快照恢复后卡池、券、保底计数、请求记录与统计都要还原且与快照不共享引用；同一请求号重复提交只返回首次结果。

验收：`python3 -m pytest -q tests` 共 15 条必须全过，不得改测试、跳过或按固定输入写死；并发超扣、过期结转与重启恢复都要自己构造场景验证；只做本地实现，不引入第三方依赖。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: spark_exchange_vault
- typeName: SparkExchangeVault
