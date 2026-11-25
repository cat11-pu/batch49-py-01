# batch49-py-safety-a-21

## 题面

线上对象存储网关的磁盘配额治理上周连出两次事故。第一次是某块数据盘被写满后所有上传请求一起失败，第二次是同块盘在十分钟里连发四十多条告警，值班同学把告警群设成了静音，真正的硬限拒绝反而没人看见。运维要求把每类资源改成软硬双阈值：越过软限进入限速并告警，宽限窗口内不重复打扰，越过硬限直接拒绝，用量回落后按滞回时长退出限速。相关代码在 gsb/batch49-py-safety-a-21 的 softlimit_grace_governor.py，核心类是 SoftlimitGraceGovernor，异常统一用 LimitError(code, detail) 表达。

当前这个文件只声明了接口，没有任何行为，调用会直接抛 NotImplementedError，需要你补齐。同一实例会被多个上传协程共享，状态全在内存，还要能导出快照交给监管进程做重启恢复；所有时间判断使用调用方传入的 now（单调秒），不得读取系统墙钟。

公开接口：
- SoftlimitGraceGovernor(grace_seconds=30, hysteresis_seconds=60)
- define(category, soft, hard)
- admit(category, amount, now) 返回 allowed、reason、usage、remaining、throttled
- release(category, amount, now) 返回释放后的用量
- observe(category) 返回当前用量
- state(category, now) 返回 normal 或 throttled
- alert_count(category)、hard_rejections(category)、soft_throttles(category)
- on_alert(callback)
- snapshot() 与 restore(data)

故障与边界：
1. 本次申请加上现有用量超过硬限时直接拒绝，返回 allowed 为 False、reason 为 hard_limit，用量一个字都不许写。
2. 放行后用量首次超过软限，进入 throttled 并触发一次告警；同一宽限窗口内再次越线不得重复告警，窗口过去后再次越线才算新告警。
3. throttled 状态下仍要放行不超过剩余额度的请求，这些放行计入 soft_throttles。
4. hard_rejections 只统计硬限拒绝，不能和软限限速混在一个计数器里。
5. define 重复定义同一类别抛 LimitError，code 为 duplicate_category；soft 必须小于 hard。
6. admit 遇到未登记类别抛 LimitError，code 为 unknown_category，不得按默认额度放行。
7. 申请量为负数抛 LimitError，code 为 bad_amount，用量保持不变。
8. 释放只能从当前用量里减，释放额由调用方保证有效，实现侧不得因此放出额外额度。
9. 用量回落到软限及以下后开始计滞回时间，满 hysteresis_seconds 才退出 throttled，中途不得提前恢复。
10. 同一实例上并发 admit 必须做到判定与扣减原子，任何时刻用量都不能超过硬限。
11. 告警回调抛异常不得阻断限速与放行，告警条数照常累加，异常吞掉即可。
12. snapshot 至少含各类别用量、状态、告警窗口起点与软限越线时刻；restore 之后这些状态继续生效，滞回计时不能从头开始。

约束：状态只能沿 normal 到 throttled 再回 normal 单调切换；硬限之下绝不接受超额放行；只允许标准库，单进程内使用。

验收：在仓库根执行 python3 -m pytest -q tests 必须全部通过；不得修改或删除用例、不得跳过、不得针对固定输入写死；并发与快照恢复场景自行验证；只做本地实现，不引入第三方依赖。

## 运行方式

在本包目录执行：

```bash
python3 -m pytest -q tests
```

## 平台字段

- name: batch49-py-safety-a-21
- domain: 安全
- sub: 系统安全
- type: Bugfix
- topic: 资源软硬双阈值治理：软限触发限速与告警、宽限窗口内不重复告警、硬限直接拒绝
- module: softlimit_grace_governor
- typeName: SoftlimitGraceGovernor
- file: softlimit_grace_governor.py
- blind_spots:
  - 用量恰好等于硬限时是否放行需与软限边界口径一致，不能用大于等于与大于混用
  - 滞回期间用量再次越软限，应从新的越限时刻还是原窗口继续计时
  - 归还量大于已用量时用量归零但不能变成负数并放出额外额度
