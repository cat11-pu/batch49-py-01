# batch49-py-safety-c-02

## 题面

值守群里这周炸了三次：双因子登录偶尔让同一个验证码连过两次，事后翻审计也查不出哪一步被用过；移动端断网重连后，一批预取好的验证码凭空作废，用户只能干等下一轮；换绑手机之后旧认证器的验证码居然还能过，新旧纪元的流水混成一片；还有几台设备被 NTP 把时钟拉回去，回拨区间里的老验证码又被放行。范围是 gsb/batch49-py-safety-c-02 下的 totp_step_ledger.py，类 TotpStepLedger，按账号分片记录（账号，时间步）的一次性消费，副本先向中心账本预取一段步租约再本地核销。

公开接口：TotpStepLedger(*, shard_capacity=64, lease_ttl_ms=30000)；register_account(account, *, epoch=1, drift_steps=1, step_ms=30000, retention_steps=8) -> AccountConfig；consume(account, *, epoch, now, lease_id=None) -> ConsumeResult；lease(account, *, now, count=1) -> StepLease；release(lease_id, *, now) -> int；rebind(account, *, now) -> int；purge_expired(now) -> tuple；consumed_steps(account, *, epoch=None) -> tuple；lease_of(lease_id)；stats() -> dict；snapshot()、restore(state)。now 一律为整数毫秒。拒绝抛 TotpError(code, detail)，码为 TOTP_INVALID_REQUEST、TOTP_UNKNOWN_ACCOUNT、TOTP_LEASE_EXHAUSTED、TOTP_LEASE_UNKNOWN、TOTP_CAPACITY_EXCEEDED、TOTP_SNAPSHOT_INVALID。

需求与边界：
①构造参数 shard_capacity、lease_ttl_ms 必须为正；register_account 的 epoch、step_ms、retention_steps 均须为正，drift_steps 不得为负，账号为空或任一项越界抛 TOTP_INVALID_REQUEST，失败不落账号。
②register_account 建立账号配置并返回 AccountConfig；重复登记只更新配置项，已消费步、纪元与租约保持不变。
③consume 要求入参 epoch 等于账号当前纪元，不一致返回 epoch_mismatch，不消耗任何步，也不改动账本。
④consume 以 now // step_ms 取当前步，候选窗为 [max(0, 当前步-drift_steps), 当前步+drift_steps]，按步号从小到大取第一个未消费步；窗内全部消费过返回 replay。
⑤成功消费返回 ok 并带 step 与 epoch，同时推进该账号已见的最大步；now 明显落后于已见最大步时返回 clock_rollback，不得按新时间重新开窗。
⑥带 lease_id 的 consume 只在该租约剩余步里挑；租约不存在、已释放或已过期返回 lease_unusable 且不消耗步；租约内剩余步消费完返回 replay。
⑦lease 从当前步向后预取连续的、未被消费也未被其它租约占用的步，返回含 lease_id、账号、纪元、步元组与 expires_at 的租约；可用步不足 count 抛 TOTP_LEASE_EXHAUSTED，账号不存在抛 TOTP_UNKNOWN_ACCOUNT，预取后已消费加占用超过 shard_capacity 抛 TOTP_CAPACITY_EXCEEDED。
⑧release 归还租约内尚未消费的步并返回归还条数；未知、已释放或已过期租约抛 TOTP_LEASE_UNKNOWN。
⑨已消费步数达到 shard_capacity 时 consume 返回 capacity 且不消费，清理之后额度释放。
⑩rebind 把纪元加一并返回新纪元，旧纪元的已消费步整体作废、该账号名下租约全部作废，未知账号抛 TOTP_UNKNOWN_ACCOUNT。
⑪purge_expired 返回 (裁掉的已消费步数, 回收的过期租约数)：早于 当前步-retention_steps 的消费记录删除，expires_at 不晚于 now 的租约回收。
⑫stats 固定返回 accounts、consumed、replays、rollbacks、epoch_rejects、lease_misses、lease_issues、lease_releases、lease_expiries、rebinds 十项；snapshot 深拷贝，restore 先整体校验再落盘，结构不合法抛 TOTP_SNAPSHOT_INVALID 且现场不变。

硬约束：同账号并发 consume 必须在锁内完成取步与记账，任何一步只能被一个调用拿到；租约到期未用的步必须回到池子，不能让副本永久占位；换绑与快照恢复之后纪元、已消费步、租约与计数必须一致。

验收：python3 -m pytest -q tests 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: totp_step_ledger
- typeName: TotpStepLedger
