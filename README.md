# batch49-py-safety-a-09

## 题面

上线新的运行时防护后，同一个容器在两台机器上拿到的系统调用策略不一致：本来只允许 open 带固定参数，合并后基础层的参数白名单被例外层的一条放行盖掉了，审计里出现大量越权调用；另一次把空策略和一份只写了默认动作的配置一起下发，显式放行的条目全丢，容器直接起不来。范围是 gsb/batch49-py-safety-a-09 的 seccomp_profile_merger.py，类型 SeccompProfileMerger，把基础层、工作负载层与例外层按优先级合并成一份生效策略，异常统一抛 SeccompMergeError(code, detail)。

数据结构与接口：Rule(syscall, action, args=())，args 是 (参数序号, 比较符, 值) 三元组元组；Layer(name, default_action, rules=())；MergedProfile(default_action, rules, origins, conflicts, generation)；merge(layers)；explain(merged, syscall)；diff(old, new)；snapshot(merged)；validate(layer)；current()；activate(merged)；conflicts(merged)；resolve_args(left, right)；action_rank(action)；stats()。explain 返回 syscall、action、decided_by、conflict；冲突条目含 syscall、layers、reason；diff 返回 added、removed、changed；snapshot 含 default_action、rules、origins、conflicts、generation；stats 至少含 merges。

要修的行为：①默认动作取所有层里最严的一个，严格程度从高到低是 deny、errno、trace、log、allow，空 profile 与非空 profile 合并时，默认动作取严之后仍要保留另一层的显式 allow 条目；②同一 syscall 多层出现时 deny 优先，allow 不能把 deny 放宽，冲突里要保留被放宽那条的来源；③例外层放行而基础层禁止时结果仍禁止，并记一条冲突；④同层同为 allow 时参数约束取交集，序号、比较符、值都相同视为一致，例外层声明 allow 但没写参数、基础层对同 syscall 有参数白名单时，合并后参数白名单必须保留；⑤参数约束无法求交时整条规则丢弃，冲突里写清涉及的两层与原因，layers 按层名字典序排列，两层分别约束参数高位与低位时求交后应得到完整区间，不能只剩其中一层的条件；⑥输出按 syscall 名和参数签名稳定排序，同一输入重复合并逐字节一致，打乱层内顺序也不影响结果；⑦每条结果带来源层，origins 与 rules 一一对应；⑧explain 能回答某个 syscall 由哪一层决定，没有被覆盖时返回 default；⑨层内重复条目先去重，参数不同按冲突处理，不是后者覆盖前者，两条来源都要进冲突清单；⑩代次由规范化内容决定，同内容同代次，内容变化代次递增；⑪激活只接受不小于当前生效代次的策略，activate 返回被激活的代次，回退抛 SECCOMP_STALE_GENERATION，生效按代次整体替换；⑫层数、条目数与单条参数个数都有硬顶，非法动作、非法 syscall 名、空层名分别抛 SECCOMP_ACTION_UNKNOWN、SECCOMP_SYSCALL_INVALID、SECCOMP_LAYER_NAME_INVALID，超限抛 SECCOMP_LIMIT_EXCEEDED，任何一项超限都要在合并开始前拒绝。

硬约束：合并是纯函数，同一输入可重复得到同样结果；生效策略按代次整体替换，不允许半新半旧；并发 merge 与读取只能看到某一代的完整结果。验收：python3 -m pytest -q tests 的 15 条全过，不改测试、不跳过、不按固定输入写死；并发合并自行验证；只做本地实现，交源码、跑测记录与一页以内的优先级说明。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: seccomp_profile_merger
- typeName: SeccompProfileMerger
