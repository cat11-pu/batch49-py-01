# batch49-py-safety-c-09

## 题面

密码重置窗口接连出岔子：一个账号在冷却期里连开三张工单，三班人各审一遍，最后用了最早那张的旧材料；另一位用户重置完成的那一刻工单刚好过期，系统把已经生效的会话失效又撤了回去，旧手机还能继续用；还有人只交了邮箱材料，审核员心软点了通过，确认动作拖到有效期之外，流程仍然放行。问题落在 gsb/batch49-py-safety-c-09 的 password_reset_procedure.py，类 PasswordResetProcedure，按账号受理重置工单、收集证明材料、评审审批、完成重置并失效旧会话。窗口只有两个人值班，一张工单的先后顺序必须留下清楚的痕迹。

公开入口：PasswordResetProcedure(*, required_level=2, validity=10, cooldown=6, approval_ttl=4, max_cases=32, notification="email")；open_case(account, *, now=0)；submit_evidence(account, kind, *, now=0)；review(account, *, now=0) 返回 ReviewResult(status, granted_level, required_level, missing)；approve(account, reviewer, *, now=0)；complete(account, *, now=0) 返回 CompleteResult(status, case_id, sessions_invalidated, notified_via)；expire(now=0)；register_session(account, session_id)；active_sessions(account)；stats()；snapshot()；restore(state)。拒绝统一抛 ResetError(code, detail)，类名、方法名与参数名必须与上面完全一致。

要落地的行为：①open_case 给账号建唯一工单，编号从 RST-0001 递增，初始 status 为 collecting、level 为 0、evidence 为空，expires_at 为 now 加 validity、cooldown_until 为 now 加 cooldown；账号去空白后为空抛 RESET_INVALID。②活跃工单数达到 max_cases 时新账号开单抛 RESET_CAPACITY；同一账号未结工单只能有一张。③submit_evidence 只接受 email、phone、manual 三种材料，其它抛 RESET_EVIDENCE_INVALID，未知账号抛 RESET_CASE_UNKNOWN，重复材料不重复计数。④证明材料按字母序去重保存，等级实时重算：manual 为 3，email 与 phone 齐备为 2，只有其中之一为 1，全无为 0。⑤review 在材料不足时返回 status 为 downgraded 与缺失材料列表，不直接拒绝；材料齐备时返回 eligible 并把工单置为可审批。⑥review 结果为 downgraded 时 approve 抛 RESET_INSUFFICIENT_EVIDENCE，从未 review 过抛 RESET_NOT_REVIEWED。⑦approve 要求 reviewer 去空白后非空，成功把工单置为 approved、记录审核人与 approved_until 为 now 加 approval_ttl，approvals 加一。⑧complete 只接受 approved 工单，否则抛 RESET_NOT_APPROVED；成功把工单置为 completed，清空该账号全部会话并返回失效数量与通知渠道。⑨工单到期由 expire 显式标记为 expired 并计数，对已过期工单再 review、approve、complete 一律抛 RESET_CASE_EXPIRED。⑩冷却期内同一账号再次申请必须复用原工单并顺延有效期，不能同时存在两张有效工单。⑪工单完成时恰好过期不得回滚已经执行的会话失效，只能如实记录完成与过期谁先发生。⑫人工审核通过但确认动作发生在有效期之外时必须拒绝并要求重新证明，不能沿用旧审核结论。

硬约束：工单状态、材料集合与会话清空必须在同一把锁内完成，同一账号并发补材料不能丢也不能重复计数；快照深拷贝，恢复后编号序列、工单、会话与七个统计与导出时一致；每账号未结工单唯一。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证，每条行为都要能从公开入口观察到；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: password_reset_procedure
- typeName: PasswordResetProcedure
