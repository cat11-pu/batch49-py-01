# batch49-py-safety-b-19

## 题面

签名策略从全局一路继承下来，上周发布时命名空间把最小签名数从 3 改成了 1，评审系统没有拦，整份策略也没有作废，结果一个签名就放行了生产制品；密钥过期后签名照旧被判有效，而真正被撤销的密钥又因为签名时间早于撤销时刻继续生效；更怪的是策略改完之后同一个签名集合仍然命中旧结论，页面显示通过，重新发布才恢复正常。范围是 gsb/batch49-py-safety-b-19 的 signature_policy_evaluator.py，类型 SignaturePolicyEvaluator，负责全局、组织、命名空间三层签名策略的解析与求值。

公开接口（签名不变）：SignaturePolicyEvaluator()；set_global_policy(policy)；set_org_policy(org, policy)；set_namespace_policy(org, namespace, policy)；compile(org, namespace) 返回 CompiledPolicy；policy_version(org, namespace)；register_key(key_id, *, roles, usages, not_before, not_after)；revoke_key(key_id, *, at)；key_status_version()；evaluate(org, namespace, signatures, *, at) 返回 Evaluation；cache_size()；snapshot()；restore(state)；stats()。拒绝时抛 PolicyError(code, detail)。

要修的点：①三层策略按全局、组织、命名空间叠加，阈值取各层最大，子层只能抬高不能压低；②子层把阈值调低的设置必须被拒绝并回落到上层值，而不是让整份策略作废或静默接受；③必需角色只增不减，按各层并集累计，允许的密钥用途只减不增，按各层交集收缩，合并后的角色与用途按字典序排列；④非法策略（阈值为零或负数、角色含空串、用途不是字符串列表、出现未知字段）报 POLICY_INVALID，且不产生新版本；⑤没有全局策略时编译或设置下层一律报 POLICY_NOT_CONFIGURED；⑥策略编译成带版本号的快照，版本号全局单调递增，每次成功设置加一，编译结果记录本次生效的层级；⑦密钥注册记录角色、用途与生效窗口，注册和撤销都推进密钥状态版本，非法密钥不推进；⑧求值统计满足角色与用途要求的有效签名数，同一密钥的多个签名只计一次；⑨签名时间早于密钥生效时间、密钥不存在、角色不符、用途不符都要逐条给出原因码，并按输入顺序汇总；⑩密钥已过期但签名时间落在密钥有效期内仍算有效，密钥被撤销则一律无效；⑪结论缓存绑定策略版本与密钥状态版本，任一变化后旧结论必须失效，同一组签名不得复用旧结论；⑫快照包含策略、密钥、两个版本号与缓存，恢复后阈值、状态版本与求值结果一致，坏快照报 POLICY_SNAPSHOT_INVALID 且不动现有状态。

硬约束：evaluate 会被多个发布流水线线程并发调用，同一组签名在同一对版本号下只能计算一次，其余调用命中缓存，计数不能丢；策略与密钥的变更要整体生效，读方不能看到半份新策略配旧密钥状态；stats 固定返回 policy_sets、keys_registered、evaluations、cache_hits 四个计数。

验收：python3 -m pytest -q tests 15 条必须全过，不改测试、不跳过、不按固定输入写死；并发求值与快照恢复自行验证；只做本地实现，交付源码与运行记录。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: signature_policy_evaluator
- typeName: SignaturePolicyEvaluator
