# batch49-py-fix-a-04

## 题面

周一早高峰，一个租户的批处理把节点内存吃满，内核 OOM 挑中的却是另一个租户的支付进程；事后核对配额，发现超卖出去的额度没有任何回收记录，谁先被牺牲也说不清依据。问题落在 gsb/batch49-py-fix-a-04 的 memquota_tenant_ledger.py，类 MemQuotaTenantLedger，用它把租户内存配额的入账、超卖回收与 OOM 选择做成可复算的账本。cgroup 用内存账本模拟，不调用真实内核接口。

公开入口：MemQuotaTenantLedger(*, global_limit, max_tenants=16)；add_tenant(name, *, quota, floor=0, priority=100)；remove_tenant(name)；charge(tenant, *, pid, bytes)；release(charge_id)；usage()；tenant_usage(name)；charges(tenant=None)；enforce()；oom_candidates(*, limit=3)；resize_global_limit(limit)；snapshot()；restore_state(state)；stats()。拒绝与失败统一抛 MemQuotaError(code, detail)。

要落地的行为：①租户名非空，quota 为正整数，floor 不小于 0 且不超过 quota，priority 为非负整数，否则 MEM_INVALID_REQUEST；同名报 MEM_TENANT_EXISTS，租户总数受 max_tenants 硬顶，超限 MEM_CAPACITY_EXCEEDED。②charge 要求租户存在、pid 与 bytes 为正整数，否则 MEM_TENANT_UNKNOWN 或 MEM_INVALID_REQUEST；单块计费不得把租户用量推过 quota，越界整块拒绝并报 MEM_QUOTA_EXCEEDED，detail 固定含 tenant、used、requested、quota 四个键。③计费块编号形如 chg-1，序号全局自增；charges 按序号升序，可按租户过滤；usage 是全局用量，tenant_usage 是单租户用量。④release 按块整块归还并返回字节数，同一块二次释放报 MEM_CHARGE_UNKNOWN，用量与块列表必须同时回退。⑤floor 是保障线：平稳回收只能挑“释放后该租户用量仍不低于 floor”的块，整块跨过保障线的块不参与回收，也不许拆半。⑥全局用量小于或等于 global_limit 时 enforce 不做任何动作，返回空回收列表、oom 为 None、unresolved 为 False，usage_before 与 usage_after 相同；只有严格超过上限才触发回收。⑦回收顺序为 priority 降序（数值越大越先牺牲），同 priority 按字节数降序，再按入账序号升序；每次整块释放，直到全局用量回到上限以内或没有可回收块。⑧回收后仍超限时选一个 OOM 目标：在剩余计费块中按同样的 priority 降序、字节降序、序号升序取第一块，返回 OomAction 带 tenant、pid、charge_id、bytes、priority 与固定 reason；选中 OOM 不会由账本自动释放该块，用量要等调用方显式 release 才回落；没有任何剩余块时 oom 为 None 且 unresolved 为 True。⑨oom_candidates 按同一顺序列出当前可牺牲的块，limit 为正整数，越界报 MEM_INVALID_REQUEST。⑩remove_tenant 只允许在租户用量为 0 时执行并返回释放字节数，仍有计费块报 MEM_TENANT_BUSY；resize_global_limit 只接受正整数，否则 MEM_INVALID_REQUEST。⑪stats 固定返回 tenants、charges、usage、global_limit、enforce_calls、reclaimed_charges、oom_selected 七个键。⑫快照深拷贝租户参数、计费块与计数，恢复后用量、块序号与统计必须复原；非法结构报 MEM_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：add_tenant、charge、release、enforce、oom_candidates、resize_global_limit 共用一把可重入锁，并发入账不得突破单租户 quota，也不得产生重复块号；enforce 执行期间不能让读方看到“块已释放但用量未回退”的中间态。快照恢复后租户参数、计费块、全局上限与统计必须完整复原，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: memquota_tenant_ledger
- typeName: MemQuotaTenantLedger
