# batch49-py-safety-c-15

## 题面

市场部上周接了三个客户，同一个邮箱域名下的人第一天登录就报错：有人被建到默认租户，有人被要求走本地注册；已经存在本地账号的员工被另开了一个影子账号，之后权限对不上。合并申请发出去了，冷却期内用户重复登录又发了一封确认邮件，收件人以为是钓鱼。更离谱的是一次压测把同一租户的创建打到几十个，风控完全没拦住。范围是 gsb/batch49-py-safety-c-15 的 jit_provisioning_gate.py，类 JitProvisioningGate，负责域名归属、即时创建、属性映射与账号合并冷却。

公开接口：JitProvisioningGate(*, cooldown=3600, rate_limit=10, rate_window=3600)；register_domain(*, domain, tenant, verified=True)；register_mapping(*, tenant, target, source, default=None, priority=0)；resolve_tenant(*, email)；map_attributes(*, tenant, attributes)；provision(*, email, attributes, now)；get_account(account_id)；find_account(*, email)；request_merge(*, account_id, email, now)；confirm_merge(*, request_id, now)；stats、snapshot、restore。拒绝抛 JitProvisionError(code, detail)，时间为整数秒。

故障与边界：①域名统一小写保存，重复注册报 JIT_DOMAIN_DUPLICATE，空域名或空租户报 JIT_INVALID_REQUEST；②邮箱大小写不敏感，格式不合法报 JIT_INVALID_REQUEST，只有命中已验证域名才计入 resolved；③域名归属存在父子域重叠时以最长匹配为准，未验证的子域不能借用父域归属，未验证域名报 JIT_DOMAIN_UNVERIFIED；④未注册域名不创建账号，provision 报 JIT_DOMAIN_UNKNOWN 并计入 rejected，resolve_tenant 返回 None；⑤属性按映射表落库，缺失字段用默认值，sources 要记录每个字段来自哪个源属性或 default；⑥同一目标字段有多个来源时高优先级胜出，同优先级且值不同报 JIT_MAPPING_CONFLICT，不允许静默取一个；⑦每租户在 rate_window 秒内最多创建 rate_limit 个账号，超限报 JIT_RATE_LIMITED，计数按滑动窗口而不是固定整点，创建失败不能占额度，窗口滑出后恢复；⑧新账号按 acct-0001 递增编号，合并请求按 merge-0001 递增编号，创建成功返回 created，合并请求初始状态为 pending，属性与来源要能原样读回，按邮箱查找不区分大小写；⑨同邮箱已存在本地账号时按策略进入合并而不是新建；已存在账号的邮箱大小写或别名不同但指向同一人时必须进入合并流程，合并后原账号的属性不能被覆盖；⑩合并请求有冷却期，确认时间等于请求时间加 cooldown，冷却期内 confirm 报 JIT_MERGE_COOLDOWN，未知请求报 JIT_MERGE_NOT_FOUND，已确认的重复确认报 JIT_MERGE_ALREADY_DONE；⑪合并冷却期内重复登录只能复用待确认的合并请求，不能重复发确认或另建账号；⑫快照深拷贝，顶层字段固定为 version、domains、mappings、accounts、merges、stats，恢复结构不符抛 JIT_SNAPSHOT_INVALID 且原状态不动。

硬约束：同一租户的创建计数与同一账号的合并请求必须串行，速率窗口在并发下不得超发；合并确认与并发首次登录交错时不能同时建号和合并；属性映射的优先级与默认值在恢复后要能重放，快照恢复后域名、映射、账号、合并请求与全部计数一致。stats 返回 domains、resolved、provisioned、merge_requests、merged、rate_limited、rejected、accounts，其中 accounts 为当前账号总数，rejected 只统计因域名不可用而拒绝的创建。

验收：`python3 -m pytest -q tests` 共 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: jit_provisioning_gate
- typeName: JitProvisioningGate
