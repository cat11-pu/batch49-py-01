# seccomp 例外收回口径说明（SeccompWaiverLease）

## 1. 数据模型：基础规则与例外覆盖分离
- `set_profile(profile, rules)` 只写 profile 的**基础规则**，内容存独立副本，本模块任何流程都不原地修改它。
- active 例外不回写基础规则，只在 `effective()` 计算时以覆盖层把对应 syscall 临时改成 `allow`。
- 因此收回（revoke / sweep / restore 补收）唯一动作是把该例外的状态从 active 翻成终态，覆盖随之消失，syscall 自动回到基础规则的原决定（deny 或基础规则中已有的 allow）。**不存在“顺手收掉别人放行”的路径。**

## 2. 收回的两种触发与唯一状态判定
- 人工 `revoke` 与到期 `sweep` 共用同一判定：仅 `state == active` 可收回，统一经内部终态翻转函数落账；终态为 `revoked`（人工）或 `expired`（扫描/启动补收/孤儿）。
- 对非 active 例外再收回一律抛 `WAIVER_STATE_INVALID`，所以人工与扫描并发时**有且仅有一次**收回成功，另一方报状态非法；所有操作在同一把 `RLock` 内原子完成。

## 3. 到期边界与批次
- 到期判定为 `expires_at <= now`（边界重合即收）。`sweep` 先在锁内快照当前候选再翻转，扫描进行中同毫秒到达的新申请不在候选集内，**不会被旧扫描误收**。
- 单批最多处理 `sweep_batch` 条，按 `(expires_at, waiver_id)` 排序，返回实际收回条数；没收完下轮继续，无到期返回 0。
- pending 例外到期未审批也由 sweep 一并清理（不产生过覆盖，只翻状态并计入 `expired`）。

## 4. profile 替换与删除
- `set_profile` 整体替换基础规则时不清空任何未到期例外：新规则里该 syscall 仍为 deny 时例外继续生效；例外覆盖与基础规则同名键互不污染。
- `remove_profile` 后例外成为孤儿：`effective` 对未知 profile 返回空规则；孤儿（含 pending/active）由下一次 sweep 无异常清理并计入 `expired`，不阻断扫描。

## 5. 重启一致性
- `snapshot()` 落盘三部分：profiles（基础规则）、waivers（例外全量）、stats（六项计数），另带 `version` 与 ID 序列 `seq`，均为深拷贝。
- `restore()` 后立即按当前 `now` 补收所有已到期及孤儿例外（启动补收不受批次限制），保证**重启后不存在已过期却仍放行**的例外；未到期例外恢复后继续放行，返回 `{"restored": n, "swept": m}`。
- ID 由单调序列生成并随快照保存，恢复后续号不复用；续期到顶后 revoke 再申请一律发放新 ID，旧例外不可复活。
