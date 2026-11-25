# batch49-py-fix-e-02

## 题面

客服收到反馈：有人在群里发了一张设备激活截图，用户码被他人抢先输入并批准，真正的用户再输入时页面只报已处理；另一个客户端把轮询间隔压到零点二秒，服务端每次都回授权待决，日志里没有限速记录；还有用户换手机后拿旧刷新令牌连续换了两次访问令牌，居然都成功。审计发现这套流程既没有把用户码的第一次批准固定到某个主体，也没有按客户端维度限制待决设备码数量，刷新令牌也没按族追踪。入口在 gsb/batch49-py-fix-e-02 的 device_code_poll_guard.py，类 DeviceCodePollGuard，负责用户码规范化、设备码签发、批准与拒绝、轮询节奏控制、令牌签发与刷新族管理。

公开入口：DeviceCodePollGuard(*, interval=5, expires_in=600, slow_down_step=5, max_interval=60, max_pending_per_client=8, max_verify_attempts=5, verify_lock_window=300, token_ttl=300, max_bindings=32)；normalize_user_code(raw)；issue(client_id, scopes, *, now=0, device_code=None, user_code=None)；approve(user_code, *, subject, now=0)；deny(user_code, *, now=0)；poll(device_code, *, client_id, now=0)；refresh(refresh_token, *, client_id, now=0)；revoke(token, *, now=0)；bindings()；describe(device_code)；purge(*, now=0)；snapshot()；restore(state)；stats()。拒绝统一抛 DeviceCodePollError(code, detail)。

要落地的行为：①normalize_user_code 去掉空格与连字符、转大写；有效字符只允许除 I、O 外的大写字母与 2 到 9 的数字，恰好 8 个，返回前四后四用连字符连接的规范形式；长度、字符集或夹杂其他分隔符报 DCP_USER_CODE_INVALID。②issue 要求 client_id 非空、scopes 去重后升序；显式 device_code 至少 16 字符否则报 DCP_DEVICE_CODE_INVALID，不传用 secrets 生成至少 32 字符；显式 user_code 先规范化，已被占用报 DCP_USER_CODE_TAKEN；返回 device_code、user_code、expires_at 等于 now 加 expires_in、interval、verification_uri（https 开头）、scope；同一客户端待决设备码达到 max_pending_per_client 报 DCP_CAPACITY_EXCEEDED。③approve 对不存在或规范化失败的用户码计入 verify_failures 并报 DCP_USER_CODE_UNKNOWN；连续失败达到 max_verify_attempts 后进入 verify_lock_window 的锁定，锁定期内即使输入正确的用户码也拒绝并报 DCP_CODE_LOCKED；now 大于等于 expires_at 报 DCP_EXPIRED；首次批准把主体绑定到设备码，返回 status 为 approved、already_approved 为假、device_code；同一主体重复批准返回 already_approved 为真且不改变绑定；不同主体报 DCP_SUBJECT_MISMATCH；已拒绝的码报 DCP_ALREADY_RESOLVED。④deny 只对待决设备码生效，返回真并把状态改为 denied；重复拒绝返回假；已批准报 DCP_ALREADY_APPROVED；过期报 DCP_EXPIRED。⑤poll 判定顺序固定，返回至少含 status、interval、retry_after：未知设备码报 DCP_DEVICE_CODE_UNKNOWN；客户端不符报 DCP_CLIENT_MISMATCH；已兑换一律报 DCP_TOKEN_CONSUMED，不再判间隔；设备码到期返回 status 为 expired_token；距上次不足当前间隔返回 slow_down，把 interval 增加 slow_down_step 且不超过 max_interval，retry_after 为新间隔；到达间隔且待决返回 authorization_pending，retry_after 等于当前 interval；已拒绝返回 access_denied；已批准则签发令牌。⑥令牌响应含 access_token、refresh_token、token_type 为 Bearer、expires_in 等于 token_ttl、scope 为签发时的升序列表；令牌绑定 client_id、subject 与 scope；兑换后设备码不可再用。⑦refresh 先校验客户端（不符 DCP_CLIENT_MISMATCH），再校验族状态；有效刷新返回新令牌并立刻让旧访问令牌失效；已用过的刷新令牌重放报 DCP_REFRESH_REUSED 并撤销整个令牌族，被撤销族报 DCP_TOKEN_REVOKED。⑧revoke 接受访问或刷新令牌，撤销整族返回真；同一令牌再次撤销返回假；活动绑定列表清空；stats 的 revocations 累加。⑨bindings 只列未撤销未过期绑定，按签发时间与 access_token 升序，条目含 access_token、client_id、subject、scopes、expires_at、family。⑩describe 返回设备码的 client_id、state、interval、expires_at、subject，未知报 DCP_DEVICE_CODE_UNKNOWN，不得返回令牌材料。⑪purge 清除 now 大于等于 expires_at 的设备码与用户码（含已批准未兑换的），返回清除条数，重复调用返回 0；不清除活动令牌。⑫snapshot 深拷贝设备码表、用户码索引、刷新索引、令牌绑定、撤销族、计数与配置；restore 结构不符报 DCP_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 issued、approved、denied、polls、tokens_issued、slow_downs、refreshes、revocations、verify_failures，denied 累计 deny 成功次数。

硬约束：poll、approve、refresh、revoke 与快照写入必须在同一把可重入锁内完成；同一个已批准设备码被多个线程同时轮询时，只能有一个线程拿到令牌，其余一律 DCP_TOKEN_CONSUMED，令牌数量不得翻倍。顺序与幂等：批准与拒绝必须幂等，轮询间隔只增不减，同一刷新令牌只能用一次；设备码兑换、刷新轮换与撤销不得出现半套状态，容量判定先于写状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: device_code_poll_guard
- typeName: DeviceCodePollGuard
