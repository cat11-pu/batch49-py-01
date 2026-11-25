# batch49-py-safety-c-08

## 题面

值班这周被撞库打穿：同一来源网段轮着换账号试密码，每个账号只错一次，账号维度永远到不了阈值，来源维度也没人管；某个账号好不容易被锁，解锁预约还没到期又挨了一次尝试，预约却照旧解除；管理员给一个账号开了豁免，来源和设备明明还在锁定期，登录却直接放了过去。问题落在 gsb/batch49-py-safety-c-08 的 layered_lockout_ladder.py，类 LayeredLockoutLadder，按账号、来源网段、设备三个维度累计失败并按各自阶梯锁定。

公开入口：LayeredLockoutLadder(*, ladders=None, max_subjects=64, max_appointments=8, decay_after=20)，默认阶梯账号 ((3,2),(5,6),(8,12))、来源 ((6,4),(10,10))、设备 ((5,3),(9,9))，每档为 (失败阈值, 锁定时长)；record_failure(account, source, device, *, now=None) 返回 FailureOutcome(locked, dimension, until, counts, tier)；record_success(account, source, device, *, now=None) 返回 LockStatus(locked, dimension, until, counts, exempt)；lock_state(account, source, device, *, now=None) 返回同一结构；dimension_config(dimension) 返回该维度的 (阈值, 时长) 档位表；grant_exemption(account, *, reason, ticks, now=None) 返回 Exemption(account, reason, expires_at, previous_until)；schedule_unlock(account, *, ticks, now=None) 返回 Appointment(account, scheduled_at)；tick(steps=1)；stats()；snapshot()；restore(state)。拒绝统一抛 LockoutError(code, detail)。

要落地的行为：①三个主体去掉首尾空白后都必须非空，否则抛 LOCKOUT_INVALID；每个维度独立索引，主体数达到 max_subjects 后新主体抛 LOCKOUT_INDEX_FULL，已登记主体照常累计。②每次失败账号、来源、设备三个计数各加一；同一主体距上次失败达到 decay_after 才发生时，计数从 1 重新开始。同一来源轮换不同账号时，账号维度各只失败一次，来源维度必须累计并按自己的阶梯先锁定。③阶梯按 (阈值, 时长) 列表取已达到的最高一档，锁定到期为当前 tick 加该档时长；返回的 tier 为档位下标，从 0 开始。④多个维度同时命中时取到期最晚的一档，作为返回与 lock_state 的 dimension 与 until；到期相同按账号、来源、设备顺序取先者。⑤locks 只记新形成的锁；已处于锁定期的同一主体继续失败只累加计数、按更高档顺延到期，不算新锁。⑥record_success 清零账号维度计数并解除账号锁，来源与设备计数保持不动，返回解除后的状态。⑦lock_state 按当前 tick 判断锁定，until 小于等于当前 tick 视为已解除但计数保留；管理员豁免在有效期内让账号维度视为未锁。⑧schedule_unlock 只接受锁定中的账号，ticks 必须为正；同一账号重复预约、预约队列超过 max_appointments 分别抛 LOCKOUT_APPOINTMENT_DUPLICATE 与 LOCKOUT_QUEUE_FULL。⑨tick 推进时钟，先执行到期的解锁预约并解除对应账号锁，再清理其它到期锁，每次解除都让 unlocks 加一。⑩grant_exemption 只对锁定中的账号生效，reason 去空白后必须非空、ticks 必须为正，返回记录里带原因与实际到期，并解除账号锁。⑪管理员豁免只解除账号维度，来源与设备维度仍在锁定期时登录仍应被拒。⑫解锁预约到期前又出现新的失败时，预约必须按新阶梯顺延，不能照旧解除。

硬约束：失败计数、锁定写入与预约队列在同一把锁内完成，八个并发失败不能少计，同一账号只形成一把新锁；tick 与 lock_state 的读方只能看到推进前或推进后的完整状态；快照恢复后时钟、三维计数、锁、豁免、预约与六个统计必须与导出时一致。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证，每条行为都要能从公开入口观察到；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: layered_lockout_ladder
- typeName: LayeredLockoutLadder
