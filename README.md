# batch49-py-safety-c-04

## 题面

手机银行接入 CIBA 之后，客服台这周长单不断：用户点了批准，客户端过一会儿才来兑换，结果提示请求已失效，重试又回到“处理中”；有人明确点了拒绝，客户端轮询几次之后又变成 pending，用户被反复弹窗；还有同一用户在两个客户端同时发起登录时，验证码提示串到了另一笔请求上，批准落错单。范围是 gsb/batch49-py-safety-c-04 下的 ciba_request_coordinator.py，类 CibaRequestCoordinator，负责 auth_req_id 生命周期、设备提示路由与轮询节奏。

公开接口：CibaRequestCoordinator(*, per_user_limit=5, per_client_limit=10, max_interval_ms=60000)；register_device(user, device_id)；create_request(client, user, *, scope, binding_message, hint, now, ttl_ms=600000, interval_ms=5000) -> CibaRequest；route_hint(auth_req_id, *, now) -> tuple；approve(auth_req_id, device_id, *, now) -> CibaRequest；deny(auth_req_id, device_id, *, reason, now) -> CibaRequest；poll(auth_req_id, *, client, now) -> PollResult；expire(now) -> int；request_of(auth_req_id)；open_count(user=None, client=None) -> int；stats() -> dict；snapshot()、restore(state)。now 一律为整数毫秒。拒绝抛 CibaError(code, detail)，码为 CIBA_INVALID_REQUEST、CIBA_UNKNOWN_REQUEST、CIBA_CLIENT_MISMATCH、CIBA_REQUEST_NOT_PENDING、CIBA_REQUEST_EXPIRED、CIBA_ROUTE_MISMATCH、CIBA_LIMIT_EXCEEDED、CIBA_SNAPSHOT_INVALID。

需求与边界：
①构造参数三个上限都须为正；register_device 的用户或设备为空抛 CIBA_INVALID_REQUEST，重复登记幂等，users 按用户去重计数。
②create_request 要求 client、user、scope、binding_message、hint 均非空，auth_req_id 全局唯一，初始 status 为 pending，expires_at 为 now 加 ttl_ms，interval_ms 取入参；不合法抛 CIBA_INVALID_REQUEST。
③未结请求指 pending 与 approved；同一用户未结数达到 per_user_limit、或同一客户端未结数达到 per_client_limit 时抛 CIBA_LIMIT_EXCEEDED，且不产生请求。
④route_hint 把提示投给该用户已登记的全部设备，返回按字典序排列的设备号元组；同一请求重复路由返回同一元组但只计一次 hints_routed；用户无设备时返回空元组并计 hints_undelivered；未知请求抛 CIBA_UNKNOWN_REQUEST。
⑤approve 只接受 pending 请求，成功把状态改为 approved 并记录 approved_by 与批准时刻；重复批准、批准已拒绝的请求抛 CIBA_REQUEST_NOT_PENDING；设备不属于该请求的用户时抛 CIBA_ROUTE_MISMATCH，批准不得落到其它未结请求上。
⑥deny 只接受 pending 请求，reason 不得为空，成功把状态改为 denied 并记录原因；空原因抛 CIBA_INVALID_REQUEST 且请求保持 pending。
⑦poll 校验请求存在与客户端归属，未知请求抛 CIBA_UNKNOWN_REQUEST，客户端不符抛 CIBA_CLIENT_MISMATCH 且不推进任何计数。
⑧过早轮询返回 slow_down：interval_ms 翻倍但不超过 max_interval_ms，下一次允许时刻顺延为 now 加新间隔；到点轮询返回请求当前状态，间隔不再变化。
⑨pending 请求到点轮询返回 pending；approved 请求到点轮询返回 approved 并携带令牌，同时状态转为 consumed，之后轮询返回 consumed 且不再给令牌；denied 请求轮询返回 denied 与原拒绝原因。
⑩expire 把 expires_at 不晚于 now 的 pending、approved 与 denied 请求改为 expired 并返回新增条数，重复调用返回 0；过期请求不可再批准或拒绝，抛 CIBA_REQUEST_EXPIRED。
⑪批准后一直没兑换的请求仍按 expires_at 过期，过期后不得因为旧的批准状态重新发放令牌；拒绝后的轮询结果必须稳定为 denied，不能回到 pending。
⑫stats 固定返回 users、created、hints_routed、hints_undelivered、approved、denied、polls、slow_downs、delivered、expired 十项；snapshot 返回 version 与 devices、requests、stats 的深拷贝，restore 先整体校验再落盘，结构不合法抛 CIBA_SNAPSHOT_INVALID 且现场不变。

硬约束：同一 auth_req_id 的批准、拒绝与轮询必须在锁内完成状态迁移，不能出现半更新；每用户与每客户端的未结数都是硬顶，过期回收后才允许新建；快照恢复后设备、请求状态、轮询节拍与计数必须一致。

验收：python3 -m pytest -q tests 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: ciba_request_coordinator
- typeName: CibaRequestCoordinator
