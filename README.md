# batch49-py-safety-a-10

## 题面

安全评审给了三条临时放行，工单关掉之后对应的 syscall 还在放行，连夜排查发现例外根本没绑到期时间；另一位同事把同一个 syscall 又申请了一次，审批通过后两条例外同时生效，收回其中一条另一条还在；后来 profile 整体替换，之前放行的条目也找不回来了，值班只能临时把整组防护回滚到旧版本。范围是 gsb/batch49-py-safety-a-10 的 seccomp_waiver_lease.py，类型 SeccompWaiverLease，管理系统调用例外的申请、生效与到期收回，异常统一抛 WaiverError(code, detail)。

数据结构与接口：Waiver(waiver_id, profile, syscall, ticket, state, expires_at, renewals)，state 取 pending、active、revoked、expired；set_profile(profile, rules)；remove_profile(profile)；effective(profile)；request(profile, syscall, ticket, *, ttl=None, now=0)；approve(waiver_id, *, now=0)；revoke(waiver_id, *, now=0)；renew(waiver_id, *, extend, now=0)；active(*, profile=None)；sweep(now)；find(*, ticket=None, profile=None, syscall=None)；snapshot()；restore(state, *, now=0)；stats()。restore 返回 restored 与 swept 两个计数；stats 含 requested、approved、renewed、revoked、expired、restored。

要修的行为：①同一 profile 同一 syscall 只允许一条有效例外；②申请必须带非空工单号，syscall 为空抛 WAIVER_SYSCALL_INVALID，到期时间是 now 加 ttl，ttl 缺省取 default_ttl，小于等于 0 抛 WAIVER_EXPIRY_INVALID，到期时间必须晚于当前时间；③生效前校验该 syscall 在当前 profile 里确实被拒绝，已经被放行或根本不存在都抛 WAIVER_SYSCALL_ALLOWED；④生效后 effective 把该 syscall 改成 allow，其余条目维持原值，返回的是副本，改它不影响内部状态；⑤到期由 sweep 收回，收回后该 syscall 回到原决定；⑥人工 revoke 与到期扫描并发时只能收回一次，第二次抛 WAIVER_STATE_INVALID，扫描与人工收回共用同一套状态判定；⑦续期只能延长，extend 小于等于 0 抛 WAIVER_RENEW_INVALID，续期不刷新工单号，次数达到 max_renewals 抛 WAIVER_RENEW_LIMIT，续期到上限后人工 revoke 再重新申请要当成新例外，不能复用旧 ID；⑧profile 被整体替换后只移除本例外贡献的条目，被拒绝的 syscall 仍然能享受例外，替换时不能顺手把未到期的例外清空，profile 被删除后例外成了孤儿，扫描要清理并记录，不能报错退出；⑨每条例外带 ID，可按工单、profile、syscall 检索，结果按 profile、syscall、ID 排序，检索返回的是只读快照；⑩到期扫描按批处理，单批最多 sweep_batch 条，返回本次收回条数，到期时间与扫描周期边界重合时，收回后同一毫秒内的新申请不能被旧扫描误收回；⑪状态落盘，重启后未到期继续有效，启动时补收已到期未收回的例外，快照要带 profile、例外与统计三部分；⑫同 profile 有效例外数有 max_active 硬顶，计数只算 active 状态，超出抛 WAIVER_CAPACITY。

硬约束：收回只能作用于本例外写入的条目，不能把 profile 里原有的放行一起收掉；扫描与申请在到期边界上不得互相误伤；状态跨重启一致，重启后不能出现已经过期却仍然放行的例外。验收：python3 -m pytest -q tests 的 15 条全过，不改测试、不跳过、不按固定输入写死；到期扫描与重启补收自行验证；只做本地实现，交源码、跑测记录与一页以内的收回口径说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: seccomp_waiver_lease
- typeName: SeccompWaiverLease
