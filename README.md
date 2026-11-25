# batch49-py-safety-c-10

## 题面

运营发现验证码渠道乱成一锅粥：主渠道维护期间，短信降级失败后请求被直接丢掉，两条渠道的配额都扣了却没人补发；同一用户在抑制窗口内先注册后改重置，拿到的还是注册那条旧码，重置流程永远验不过；配额打满时接口只回一句超限，不告诉前端什么时候能重试，重试之后窗口计数又被清零，等于配额翻倍。问题落在 gsb/batch49-py-safety-c-10 的 otp_delivery_broker.py，类 OtpDeliveryBroker，按目标、租户与渠道分片计配额，负责验证码下发、渠道降级与校验。

公开入口：OtpDeliveryBroker(*, window=10, target_limit=3, tenant_limit=6, channel_limit=5, max_batches=32, code_ttl=5, max_attempts=3, suppression=3)；set_channel_enabled(channel, enabled)；request_code(target, *, tenant="default", purpose="login", preferred_channel="email", now=0) 返回 DeliveryBatch(batch_id, target, tenant, purpose, channel, code, created_at, expires_at, attempts_left, status, fallback_reason)；verify_code(target, code, *, purpose="login", now=0) 返回 VerifyResult(status, batch_id, attempts_left, reason)；resend(target, *, purpose="login", now=0)；channel_status()；target_usage(target, *, now=0)；purge_expired(now=0)；stats()；snapshot()；restore(state)。拒绝统一抛 OtpError(code, detail)，类名、方法名与参数名必须与上面完全一致。

要落地的行为：①批次编号从 OTP-000001 递增；target 去空白后为空、purpose 不在 login、register、reset 之列、preferred_channel 不是 email 或 sms 时一律抛 OTP_INVALID。②配额按窗口分片，窗口下标为 now 整除 window；同一窗口内 target、tenant、channel 各有上限，按 target 到 tenant 再到 channel 的顺序检查，超限分别抛 OTP_TARGET_QUOTA、OTP_TENANT_QUOTA、OTP_CHANNEL_QUOTA。③渠道选择优先用 preferred_channel，若它被停用则降级到另一条启用的渠道并把 fallback_reason 记为 primary_disabled；两条都停用时抛 OTP_CHANNEL_UNAVAILABLE，且不增加任何计数。④request_code 成功让 requested 加一、所选渠道的 sent 加一，批次 code 不为空、created_at 为 now、expires_at 为 now 加 code_ttl、attempts_left 为 max_attempts、status 为 sent。⑤抑制窗口内同一 target 同一 purpose 的重复请求返回原批次，batch_id 与 code 不变，reused 加一，不重复占配额，也不重复计渠道发送。⑥verify_code 按 target 与 purpose 找最近一条 status 为 sent 的批次，找不到抛 OTP_BATCH_UNKNOWN。⑦校验码不一致时 attempts_left 减一、返回 mismatch，减到零返回 exhausted；此后即使拿正确码来也只返回 exhausted。⑧now 到达 expires_at 时 verify 返回 expired、reason 为 code_expired，并把批次置为过期；同一批次之后再校验按不存在处理。⑨resend 忽略抑制窗口，为同一 target 与 purpose 新建批次并正常计配额，resends 加一，原批次保留；purge_expired 删除 expires_at 小于等于 now 的批次并返回数量，purged 累加。⑩备用渠道也失败时必须回到主渠道排队并保留两次尝试的配额记账，不能静默丢弃请求。⑪抑制窗口内同一 target 改用另一种用途（注册改重置）不得复用旧批次，必须新建。⑫配额耗尽时要返回可重试时间且窗口内计数保持不清零，重试时间不得早于窗口结束。

硬约束：批次创建、配额扣减与状态变更在同一把锁内完成，同一 target 同一 purpose 的并发请求只能形成一个批次并只扣一次配额；抑制窗口按 created_at 与 now 的差值判断；快照恢复后批次、渠道开关、编号序列、窗口配额与七个统计必须与导出时一致。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证，每条行为都要能从公开入口观察到；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: otp_delivery_broker
- typeName: OtpDeliveryBroker
