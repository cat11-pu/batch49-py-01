# batch49-py-safety-a-29

## 题面

进程准入网关按可执行文件选择系统调用过滤 profile，最近两次事故都出在缓存：二进制原地升级后摘要已经变化，网关仍按旧摘要命中旧策略，危险调用被放行；同一 inode 的两个硬链接分别配了不同 profile，按 inode 命中时全落到先注册的那条，路径规则形同虚设；命令行里带了口令参数，缓存键却把参数原文写进快照，安全审计直接判了高危。更麻烦的是规则更新后，已经运行的进程还拿着旧代次策略，运维无法判断哪些进程该重新绑定。范围是 gsb/batch49-py-safety-a-29 的 syscall_profile_selector.py，类 SyscallProfileSelector，按路径、inode、内容摘要与启动参数选择 profile，未匹配走默认拒绝型。

公开接口：SyscallProfileSelector(cache_size=16, max_rules=32, default_profile="deny-all", clock=None)；register(name, path=None, inode=None, digest=None, argv_prefix=None, policy=None) 注册并返回代次；unregister(name) 删除规则；generation() 返回代次；profiles() 返回规则名；select(exe_path, argv=(), now=None) 返回绑定；invalidate(path=None, inode=None, digest=None) 清缓存并返条数；binding_current(binding) 判断绑定是否仍有效；cache_stats() 返回缓存计数；snapshot()/restore(state) 保存恢复；stats() 返回操作计数。拒绝统一抛 ProfileSelectError(code, detail)。digest 为内容 SHA-256，inode 为“设备号:inode号”。

故障与边界：①匹配优先级为 digest 高于 inode，inode 高于 path，高优先级命中后不再看低优先级规则；②未命中任何规则、文件不存在或读不到时返回 default_profile 的拒绝绑定，denied 为 True，不得回退放行；③脚本带 shebang 时按解释器选择 profile，绑定里同时记录 interpreter 与 script；④同名不同内容抛 PROFILE_EXISTS，同名同内容幂等且不升代次；⑤规则表任何有效变更都升代次并清空缓存，绑定记录代次，binding_current 对旧代次返回 False；⑥缓存键含设备号、inode、摘要与参数摘要，参数不同视为不同条目，不能只按路径命中；⑦同一文件多条同级规则命中时按规则名升序裁决，其余名字放进 conflicts 并计数；⑧invalidate 按路径、inode 或摘要清掉匹配缓存并返回条数，不改变代次；⑨缓存条数按最近最少使用淘汰，规则表满抛 RULE_TABLE_FULL；⑩register 没有任何选择条件抛 INVALID_RULE；⑪快照必须深拷贝，restore 非法结构抛 STATE_INVALID 且规则、缓存与统计不变；⑫stats 固定返回 selects、conflicts、denied、registered、rejected，cache_stats 固定返回 size、hits、misses、evictions。

还有三条必须覆盖：可执行文件在 select 与 exec 之间被替换时，绑定的是旧摘要，exec 前必须复核；同一 inode 的两个硬链接分别注册不同 profile 时，按 inode 命中不能掩盖路径规则；参数摘要里可能含敏感值，缓存键与快照只能出现摘要，不能出现参数原文。已经被替换的旧缓存条目不能继续返回过时策略，读取失败按拒绝处理。

硬约束：并发 select 与 invalidate 只能看到旧代次或新代次完整策略，不能半新半旧；缓存与规则数都有硬顶，超限按淘汰或拒绝处理；快照恢复后代次、规则与统计一致，恢复失败不得改动现场。验收：python3 -m pytest -q tests 15 条全过；不得改测试、跳过或按固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: syscall_profile_selector
- typeName: SyscallProfileSelector
