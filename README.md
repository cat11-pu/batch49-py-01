# batch49-py-safety-c-01

## 题面

认证网关这两天被值班组连着挂了三条工单。晚高峰口令校验排队排到几十毫秒还不出结果，客户端只能一直转圈；少数租户的校验把工作池占满，其他租户连登录都进不来；安全组抽查还发现，低代际记录的账号一直没升到新参数，而升级任务一开就抢走校验算力。日志里同一个不存在的账号被反复打，耗时和真实账号几乎看不出差别。范围是 gsb/batch49-py-safety-c-01 下的 password_hash_budget_pool.py，类 PasswordHashBudgetPool，负责有界工作池、按租户算力预算与延后升级。

公开接口：PasswordHashBudgetPool(*, pool_capacity=8, queue_limit=8)；register_policy(tenant, *, per_tenant_concurrency, queue_deadline_ms, cost_ms, window_ms, budget_units, verify_units, upgrade_units, lockout_threshold, lock_ms) -> HashPolicy；add_record(tenant, account, secret, *, generation=1)；advance_generation(tenant) -> int；verify(tenant, account, secret, *, now) -> VerifyResult；defer_upgrade(tenant, account, *, generation)；run_upgrades(tenant, *, now, limit=None) -> list[UpgradeOutcome]；budget_of(tenant, *, now) -> int；pending_upgrades(tenant) -> list；failure_count(tenant, account) -> int；stats() -> dict；snapshot()、restore(state)。now 一律为整数毫秒。拒绝抛 PasswordHashError(code, detail)，码为 HASH_POLICY_INVALID、HASH_UNKNOWN_TENANT、HASH_INVALID_REQUEST、HASH_SNAPSHOT_INVALID。

需求与边界：
①构造参数 pool_capacity、queue_limit 必须为正；register_policy 的各项阈值都要满足正整数约束，verify_units 不得超过 budget_units，租户为空或任一项越界一律 HASH_POLICY_INVALID，失败不产生策略。
②add_record 只保存账号与口令摘要、代际，摘要只由账号与口令决定，同账号重复写入覆盖旧值。
③verify 的判定顺序固定：租户策略、入参、预算窗口结转、锁定、预算、租户并发、全局池位，最后才比对凭证；未知租户 HASH_UNKNOWN_TENANT，空租户、空账号或空口令 HASH_INVALID_REQUEST。
④池位不足且预计等待超过 queue_deadline_ms 时返回 status=retry、reason=queue_timeout，retry_after_ms 为预计等待毫秒；这类重试不计成密码错误，也不推进锁定计数。
⑤等待条目达到 queue_limit 后直接返回 queue_full 重试，retry_after_ms 取池内最早释放时刻减去当前时刻；租户在途校验达到 per_tenant_concurrency 返回 concurrency 重试，retry_after_ms 取该租户最早释放时刻。
⑥口令正确返回 status=ok、reason 为空串、generation 为记录代际，needs_upgrade 表示记录代际是否落后策略代际；成功后该账号失败计数清零。
⑦口令错误与账号不存在都返回 denied/credentials；不存在账号走哑哈希并计入 dummy，它与真实校验共用同一池与同一耗时档，池满时拒绝行为一致。
⑧连续失败达到 lockout_threshold 后锁定 lock_ms，锁定期内正确口令也返回 locked/lockout，retry_after_ms 为剩余锁定毫秒，到期自动解除。
⑨verify 每次扣 verify_units；预算不足返回 retry/budget，retry_after_ms 为窗口剩余毫秒；窗口按 window_ms 滚动后预算整体恢复。
⑩advance_generation 把当前代际加一并返回；verify 命中落后记录时自动入延后升级清单；defer_upgrade 幂等、不查预算、不丢条目。
⑪run_upgrades 按（代际，账号）升序取，逐条扣 upgrade_units，预算不够就停在原处；单条 upgrade_units 大于整窗 budget_units 时记 failed/HASH_UPGRADE_UNSUPPORTED 并计入 upgrades_failed，且不改变此前校验的结果。
⑫stats 固定返回 verifies、ok、denied、dummy、queue_retries、budget_retries、locked、deferred、upgrades、upgrades_failed 十项；snapshot 深拷贝，restore 先整体校验再落盘，结构不合法 HASH_SNAPSHOT_INVALID 且现场不变。

硬约束：同租户并发校验必须在锁内完成池位、预算与计数的合并，预算与计数不得丢；池容量、队列长度、每租户并发都是硬顶，不能靠等待绕过；快照恢复后策略、记录、预算、清单、失败与锁定记录都要在且不共享结构。

验收：python3 -m pytest -q tests 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: password_hash_budget_pool
- typeName: PasswordHashBudgetPool
