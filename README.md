# batch49-py-safety-c-12

## 题面

运维周报里连续三周出现同一条抱怨：换手机的同事被反复要求短信验证，而一台三年没登录的旧笔记本却一直保留最高信任等级。更糟的是同一台机器换了四个浏览器，安全中心把它们记成四台设备，撤销其中一条另外三条还在用。设备表还和登录风控共用，等级算错会把陌生设备直接放进免二次验证通道，客服只能手工改表兜底。范围是 gsb/batch49-py-safety-c-12 的 trusted_device_ladder.py，类 TrustedDeviceLadder，维护用户设备、信任阶梯、设备族合并与撤销。

公开接口：TrustedDeviceLadder(*, max_devices_per_user=5, max_family_members=3, idle_seconds=2592000)；register_device(*, user, device_id, stable_key, now, session_device=None)；record_login(*, user, device_id, success, method="password", now)；promote(*, user, device_id, now)；demote(*, user, device_id, now)；merge_family(*, user, stable_key, now)；revoke(*, user, device_id, now)；list_devices(user)；tick(now)；stats、snapshot、restore。拒绝抛 TrustedDeviceError(code, detail)，时间一律用整数秒，登录方式只认 password 与 mfa。

故障与边界：①新设备从 0 级开始，未合并时族标识为 solo:<device_id>，重复设备与空标识分别报 DEVICE_DUPLICATE / DEVICE_INVALID_REQUEST，登录、晋升、降级、撤销遇到未知设备报 DEVICE_NOT_FOUND；②连续成功登录累计，达到 2 次升 1 级、3 次且有 MFA 验证升 2 级、4 次且已验证升 3 级，晋升后连续计数清零，晋级门槛不满足时一次也不许跳级；③等级不足、未验证或已到顶分别报 DEVICE_PROMOTION_NOT_ELIGIBLE、DEVICE_PROMOTION_NOT_ELIGIBLE、DEVICE_ALREADY_TOP；④降级一次只降一级并清零连续计数，0 级降级报 DEVICE_ALREADY_LOWEST；⑤tick 把闲置超过 idle_seconds 的设备降一级，活跃设备不动，返回本次降级数，边界秒数按已闲置处理；⑥每用户设备数到顶时淘汰最低等级且最久未用的设备，不得淘汰当前会话设备，无候选时报 DEVICE_CAPACITY_EXCEEDED；⑦每族成员数有上限，超限报 DEVICE_FAMILY_CAPACITY；⑧merge_family 把同一稳定键的条目合并成族并让全族采用最强等级，合并后族标识为 family:<stable_key>，返回的成员按设备号排序；⑨族内任一成员被撤销必须整族失效，不能只撤销单条，revoke 返回本次失效的设备号列表；⑩晋升所需的连续成功登录期间只要有一次失败，连续计数必须重置而不是继续累加；⑪数量上限淘汰不得动当前会话正在使用的设备，只能淘汰最低等级且最久未用者；⑫list_devices 只返回未撤销设备并按设备号升序；快照深拷贝，顶层字段固定为 version、devices、stats，恢复结构不符抛 DEVICE_SNAPSHOT_INVALID 且原状态不动。

硬约束：同一用户的注册、晋升与撤销必须串行，设备数上限与族成员上限在并发下不得被突破；tick 与注册交错时不能把刚登录的设备当场降级；每次晋级、降级、合并、撤销都要落计数，设备记录里的等级、连续计数、验证标记与最后使用时间必须同步刷新，不能只改一处；快照恢复后设备、等级、族关系、连续计数与全部统计一致。stats 返回 registered、promoted、demoted、merged、revoked、evicted、active_devices、families，其中 active_devices 为未撤销设备数，families 为活跃设备涉及的族标识数，merged 统计被合并进族的设备条数，evicted 为因数量上限被淘汰的设备数。

验收：`python3 -m pytest -q tests` 共 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: trusted_device_ladder
- typeName: TrustedDeviceLadder
