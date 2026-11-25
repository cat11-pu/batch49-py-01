# batch49-py-fix-a-06

## 题面

外部渗透测试上线一个月，安全运营连着处理了三类越界：某租户的扫描作业打到了合同范围外的网段，执行记录里却写着授权通过；同一个目标被不同请求重复扣额，配额在下午三点前就见底；还有同事把域规则写成了 example.com，结果 notexample.com 也被放行。撤销一条授权之后，旧请求重放仍能继续扫描，事后没人说得清是哪一版范围规则批的。问题集中在扫描入口缺少统一的目标范围校验与配额账本，范围是 gsb/batch49-py-fix-a-06 的 scan_scope_gate.py，类 ScanScopeGate。

公开入口：ScanScopeGate(*, tenant_budget=100, max_targets_per_request=50, max_rules_per_tenant=32, window_seconds=3600)；grant_scope(tenant, *, cidrs=(), domains=(), ports=())；deny_scope(tenant, *, cidrs=(), domains=(), ports=())；revoke_scope(tenant, *, cidrs=(), domains=(), ports=(), effect="allow")；scope_of(tenant)；authorize(tenant, targets, *, request_id=None, now=0)；release(request_id, *, now=0)；remaining(tenant, *, now=0)；denials(tenant=None)；reset_window(now=0)；snapshot()；restore(state)；stats()。拒绝统一抛 ScanScopeError(code, detail)。

要落地的行为：①目标写成 host:port，IPv6 用 [addr]:port；主机名小写、去尾部点，IP 用规范文本，端口 1 到 65535；单个目标格式不合法只阻断该条，记 SCAN_TARGET_INVALID，不能拖垮整批。②允许规则分 CIDR、域后缀、端口区间三类；域后缀 api.example.com 只匹配自身和其子域，不能匹配 notexample.com；显式拒绝先于允许判定，命中拒绝记 SCAN_TARGET_DENIED。目标不在允许范围记 SCAN_TARGET_OUT_OF_SCOPE，租户一条允许规则都没有记 SCAN_SCOPE_MISSING。③端口规则支持 443 与 8000-8100，重叠和相邻区间合并；命中拒绝端口记 SCAN_PORT_DENIED，未落入允许端口记 SCAN_PORT_NOT_ALLOWED，拒绝端口优先于允许端口。④授权规则按租户分别维护，grant_scope、deny_scope 与 revoke_scope 只在规则集合真正变化时把版本加一，重复添加或撤销不存在的项返回当前版本；任一时刻允许加拒绝的规则总数受 max_rules_per_tenant 限制，超限抛 SCAN_RULE_LIMIT 且不留半套规则。⑤authorize 先校验整批：空清单或非序列抛 SCAN_BAD_REQUEST，条数超过 max_targets_per_request 抛 SCAN_BATCH_TOO_LARGE，这两种情况都不扣配额。⑥配额按租户扣减，一次请求里获准的目标数超过剩余额度时整批拒绝并抛 SCAN_QUOTA_EXHAUSTED，已扣额度和拒绝台账都不得变化；时间跨过 window_seconds 后窗口额度重置，但请求账本仍保留。⑦request_id 相同且租户与目标集合一致时返回首次决策的副本，不重复扣额、不再按新规则重算；同一 request_id 换了目标抛 SCAN_REQUEST_CONFLICT。⑧release 只归还首次成功请求的额度，重复调用或未知编号返回 0，归还量不能让已用额度变成负数。⑨同一请求里重复出现的同一目标按一条计费，allowed 只保留首次出现的规范形式；CIDR 写法带主机位时按网络地址归一并与已有规则去重，例如 10.0.0.5/24 等同于 10.0.0.0/24。⑩所有被阻断的目标按出现顺序写入拒绝台账，denials 可按租户过滤，快照恢复后台账与计数保持一致。⑪stats 至少给出 authorized、blocked、requests、replayed、released、quota_exhausted、scope_changes 七项计数，重放和拒绝要分别累加。⑫snapshot 返回深拷贝，包含租户规则、版本、已用额度、窗口起点、拒绝台账、请求账本与计数；restore 遇到结构不符抛 SCAN_SNAPSHOT_INVALID 并保持原状态可用。

硬约束：authorize、release 与规则变更共用一把可重入锁，配额检查与扣减必须在同一临界区完成，并发请求合计不得超过 tenant_budget；窗口重置与请求重放并发时，重放不得再吃新窗口的额度。快照与恢复要整体替换，恢复失败不能留下半套状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: scan_scope_gate
- typeName: ScanScopeGate
