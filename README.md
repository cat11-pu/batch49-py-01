# batch49-py-safety-a-13

## 题面

守护进程把网络管理、原始套接字这类能力按角色委派给工作进程之后，账一直不平：工作进程重启后旧委派还挂着，新进程拿着同一个 pid 直接继承了不该有的能力；有角色被删掉，回收时把别的角色占的份数也一起扣了；同一个人重复申请同一能力，台账里出现两条，汇总计数翻倍；守护进程自己的可用额度被上层削过一次，已发出的委派比新的上限还多，台账却照样往下发；回收和能力检查撞在一起时，行使方偶尔看到半份授权。这次收进 gsb/batch49-py-safety-a-13 的 capability_delegation_ledger.py，类 CapabilityDelegationLedger 记持有者、角色、能力与账本快照，异常统一为 CapError(code, detail)。进程表用 register_holder 登记，进程身份由 pid 加 start 共同确定；时间为整数秒，快照版本常量为 SNAPSHOT_VERSION=1。

公开接口：CapabilityDelegationLedger(*, max_caps_per_holder=16, max_delegations=128, max_roles=8, clock=None)；load_daemon(caps) 用能力到份数的字典重置守护进程自身额度；register_holder(holder, *, pid, start, group=None)；delegate(*, role, capability, holder, count=1, ttl=None, now=0, granted_by="daemon")；revoke(*, role, capability, holder, now=0)；remove_role(role, *, now=0)；held_by(holder) 按能力升序返回；active(capability) 返回可行使份数；summary() 返回 roles（升序元组）、capabilities（每项含 daemon、delegated、exercisable）、records（active、pending_revoke、revoked）与 reclaimed；exit_holder(holder, *, now=0)；tick(now)；snapshot()；restore(state)；stats()。Delegation 字段为 role、capability、holder、count、granted_by、granted_at、expires_at、state，state 取 active、pending_revoke、revoked。

要求逐条落地：①委派集合必须落在守护进程当前额度内，超出报 CAP_UNAVAILABLE，空字段、非正 count、非正 ttl 报 INVALID_REQUEST，未登记的持有者报 HOLDER_UNKNOWN；②同一角色、同一能力、同一持有者重复申请是幂等的，不增计数，suppressed 加一，返回原记录；③单持有者能力种数超 max_caps_per_holder 报 HOLDER_LIMIT，委派记录总数超 max_delegations 报 DELEGATION_LIMIT，角色数超 max_roles 报 ROLE_LIMIT；④记录含授予者与生效、到期时间，expires_at 等于 granted_at 加 ttl，未给 ttl 则为空；⑤持有者仍在运行时 revoke 只把记录标 pending_revoke 并返回 0，不得谎报已回收，pending 份数仍占额度但不再可行使；⑥持有者退出时按 pid 加 start 回收，exit_holder 返回被回收的记录数，份数计入 reclaimed，pid 被新进程复用时 tick 必须把旧持有者的委派全部回收且新进程不继承；⑦到期由 tick 处理，now 大于等于 expires_at 的记录回收，返回 expired、reclaimed、dead_holders 三个计数；⑧快照含 version、daemon、holders、delegations、counters，恢复不复活已回收委派，结构非法报 SNAPSHOT_INVALID 且不动现场；⑨工作进程 fork 出子进程后父进程退出，能力由子进程继承，账本要按进程组而不是单 pid 记，组内还有人就不能按退出回收；⑩同一能力委派给两个角色、其中一个角色被删除时，remove_role 只减该角色占的份数，其他角色不动；⑪守护进程自身额度被外部削减后，已发出的委派超过新的可委派上限，要报 INCONSISTENT_LIMIT，不能静默截断；⑫回收只发生一次，重复 revoke 或对已回收记录再 revoke 返回 0，不得重复累加 reclaimed。

硬约束：汇总视图在并发下不得出现负计数或超上限的瞬时值，回收与行使并发时行使方要么看到完整授权要么被拒绝；单持有者能力数、全局委派记录数、角色数都是硬顶；stats 返回 delegated、suppressed、pending_revoke、revoked、reclaimed、expired，其中 reclaimed 以份数计，其余以记录数计。

验收：`python3 -m pytest -q tests` 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: capability_delegation_ledger
- typeName: CapabilityDelegationLedger
