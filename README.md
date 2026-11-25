# batch49-py-safety-c-07

## 题面

客服升级了一批带指纹解锁的平板：固件升到新代际后，旧模板仍按老阈值放行，新模板标称的高活体阈值没人执行；连续活体失败本该进冷却，结果改走设备密码一次就绕了过去；更离谱的是策略中心把生物识别关了，用户仍被记了两次活体失败，账号直接进了锁定统计。问题落在 gsb/batch49-py-safety-c-07 的 biometric_unlock_policy.py，类 BiometricUnlockPolicy，按设备维护模板代际、活体阈值、失败计数、冷却与密码回退。时间统一用整数 now，省略时取 0。

公开入口：BiometricUnlockPolicy(*, liveness_threshold=0.80, failure_limit=3, cooldown=5, compat_window=10, max_templates=8, password_fallback_limit=2)；enroll(device, template_id, *, liveness_threshold=None, now=0)；upgrade_template(device, *, liveness_threshold, now=0)；unlock(device, template_id, liveness_score, *, now=0)；fallback_password(device, password_ok, *, now=0)；set_device_policy(device, *, biometric_enabled=None, liveness_threshold=None, password_fallback_limit=None)；device_policy(device)；list_templates(device)；stats()；snapshot()；restore(state)。拒绝统一抛 BiometricError(code, detail)，类名、方法名与参数名必须与上面完全一致。

要落地的行为：①设备首次登记模板时建立代际 1 与默认策略：生物识别启用、设备阈值取构造值、密码回退上限取构造值；未知设备的 device_policy 抛 BIO_DEVICE_UNKNOWN，而 list_templates 对未知设备返回空列表。②template_id 去空白后为空抛 BIO_INVALID_REQUEST；同一设备重复登记同一 template_id 抛 BIO_TEMPLATE_DUPLICATE；模板数达到 max_templates 再登记抛 BIO_TEMPLATE_CAPACITY。③upgrade_template 把该设备代际加一并返回新代际，本次传入的 liveness_threshold 只作用于新代际登记的模板，已登记模板保留原代际阈值。④每个模板的生效阈值取所属代际阈值与设备策略阈值中的较大者，设备策略只能抬高标准，不能放宽。⑤unlock 遇到未知设备抛 BIO_DEVICE_UNKNOWN、未知模板抛 BIO_TEMPLATE_UNKNOWN；模板状态不是 active 时返回 status 为 reenroll_required 的结果，不放行也不抛错。⑥分数达到生效阈值返回 status 为 unlocked、reason 为 liveness_ok，失败计数清零，unlocks 加一。⑦分数不足返回 fallback_required、reason 为 liveness_low，失败计数与 fallbacks 各加一，并允许改走密码。⑧失败计数达到 failure_limit 时本次直接返回 cooldown，cooldown_until 等于 now 加 cooldown，cooldowns 加一；冷却期内 unlock 一律返回 cooldown 且不再累加失败。⑨fallback_password 密码正确返回 unlocked 并清零失败计数，密码错误返回 password_rejected 且不改变生物失败计数，两种情况 password_fallbacks 都加一。⑩设备策略关闭生物识别时，unlock 直接返回 password_required 并提示走密码，不把这次调用算作一次生物失败。⑪兼容期结束后旧代际模板不能再验证，相关记录转为待重注册，不静默删除。⑫冷却期内即使密码正确也只允许 password_fallback_limit 次尝试，超出后按账号策略继续锁定。

硬约束：模板登记、代际切换与失败计数在同一把锁内完成，同一 template_id 的并发登记只允许一个成功，其余必须拿到重复错误；快照导出后修改快照不得影响活对象，restore 结构不符抛 BIO_SNAPSHOT_INVALID 且原状态不变；每设备模板数受 max_templates 硬顶。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证，每条行为都要能从公开入口观察到；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: biometric_unlock_policy
- typeName: BiometricUnlockPolicy
