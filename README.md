# batch49-py-safety-c-06

## 题面

安全密钥这批退货里查出一道老问题：同一把密钥昨天已经连续输错进了硬锁，拔插一次后剩余次数又回到满值，攻击者可以借着重启无限试；换过 PIN 之后旧常驻凭据照旧能列出来，同一个凭据 ID 重复登记也照收；更麻烦的是有一次在 reset 与登记并发时掉电，清掉的凭据数和计数器对不上，审计拿不出准数。问题落在 gsb/batch49-py-safety-c-06 的 fido2_pin_retry_store.py，类 Fido2PinRetryStore，负责一枚安全密钥的 PIN、重试计数、常驻凭据清单与复位。密钥是发给外部审计师使用的，每一次失败、锁定与复位都要能解释得清楚。为便于本地复跑，时间用整数 now，省略时取 0。

公开入口：Fido2PinRetryStore(pin="123456", *, max_retries=8, max_resident=25, device_id="key-0001", firmware_generation=1)；verify_pin(pin, *, now=None) 返回 PinAttempt(status, retries_left)；change_pin(old_pin, new_pin, *, now=None)；pin_status() 返回 PinState(retries_left, hard_locked)；device_info()；register_credential(credential_id, user_name, *, rp_id="default", now=None)；delete_credential(credential_id)；list_credentials()；reset(*, now=None)；stats()；snapshot()；restore(state)。拒绝统一抛 Fido2PinError(code, detail)。类名、方法名、参数名与文件名必须与上面完全一致，调用方按关键字传参与整数时间。

要落地的行为：①PIN 只接受 4 到 63 位纯数字，格式不符抛 PIN_FORMAT_INVALID，且不消耗重试次数、不动失败计数。②verify_pin 命中返回 ok 并把剩余次数复位到 max_retries；不匹配返回 pin_invalid、剩余次数减一、failures 加一。③剩余次数减到 0 的那次返回 hard_locked 并计一次硬锁，此后再调用 verify_pin 一律抛 PIN_HARD_LOCKED。④硬锁只能由 reset 解除，reset 返回清掉的凭据数、把剩余次数恢复为 max_retries、resets 加一。⑤change_pin 的旧 PIN 错误按一次失败累计，未到上限抛 PIN_INVALID，到上限抛 PIN_HARD_LOCKED；新 PIN 与当前相同抛 PIN_UNCHANGED 且不计数。⑥change_pin 成功后新 PIN 立即生效，剩余次数与失败计数一并复位，pin_changes 加一。⑦register_credential 的 credential_id、user_name、rp_id 去掉首尾空白后都必须非空，否则抛 CREDENTIAL_INVALID 且不改变任何计数。⑧重复 credential_id 抛 CREDENTIAL_DUPLICATE，常驻数达到 max_resident 再登记抛 RESIDENT_FULL。⑨list_credentials 按登记先后返回，delete_credential 成功返回 True，凭据不存在返回 False 且不计数，删除之后该 ID 才能再次登记。⑩stats 返回 verifies、failures、hard_locks、resets、registered、deleted、pin_changes 七个累计值，其中 verifies 只记成功校验。⑪reset 之后设备标识与固件代际保持不变，凭据枚举返回空列表而不是报未初始化，空列表就是没有常驻凭据的正常表述。⑫snapshot 给出可深拷贝的字典，键为 version、pin、retries_left、hard_locked、credentials、counters、device；restore 遇到结构不符抛 PIN_SNAPSHOT_INVALID，原状态不受影响。

硬约束：重试计数与凭据增删必须在同一把锁内完成并作为一次整体写入，掉电重启后不能出现计数已加而凭据已删的半状态；同一 credential_id 的并发登记只允许一个成功，其余必须拿到重复错误；读方在并发下只能看到操作前或操作后的完整状态；快照恢复后 PIN、剩余次数、硬锁标志、凭据顺序与七个计数都与导出时一致。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证，任何一条行为都要能直接从公开入口观察到；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: fido2_pin_retry_store
- typeName: Fido2PinRetryStore
