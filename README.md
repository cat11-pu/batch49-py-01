# batch49-py-fix-a-08

## 题面

办公网出口的解析服务换上新缓存后，故障单从三个方向涌来：有人访问内网系统被带到一台陌生公网地址，抓包发现应答的事务号对不上，但缓存已经把它写进去了；营销页改解析后十分钟仍返回旧地址，运维把 TTL 调到五秒，结果每秒上万次回源把上游打挂；还有一台上游进入黑洞，解析器却始终只问它，备用的那台一直闲着。更隐蔽的是，攻击者往应答里塞一条与问题无关的 A 记录，缓存照单全收，之后同网段用户都被带到同一地址。问题落在 gsb/batch49-py-fix-a-08 的 dns_cache_guard.py，类 DnsCacheGuard，负责应答校验、TTL 钳制、缓存容量与上游切换。

公开入口：DnsCacheGuard(fetcher=None, *, min_ttl=30, max_ttl=300, max_negative_ttl=60, stale_grace=15, max_entries=128, failure_threshold=2, cooldown_seconds=30, max_upstreams=4)；set_upstreams(upstreams)；active_upstream(*, now=0)；note_failure(upstream, *, now=0)；note_success(upstream, *, now=0)；resolve(name, qtype, *, now=0)；lookup(name, qtype, *, now=0)；purge(*, now=0)；snapshot()；restore(state)；stats()。拒绝统一抛 DnsCacheError(code, detail)。fetcher 以 (upstream, query) 调用，query 含 name、qtype、txid。

要落地的行为：①查询名去空白、转小写并去掉末尾的点，空名、超长名或非法标签抛 DNS_NAME_INVALID；类型转大写，只接受 A、AAAA、CNAME、TXT、MX、NS，其余抛 DNS_QTYPE_INVALID。②set_upstreams 接受非空、互不重复且不超过 max_upstreams 的字符串列表，整表替换并重置健康计数，返回数量；非法输入抛 DNS_UPSTREAM_INVALID。③上游按注册顺序选择，连续失败达到 failure_threshold 后隔离 cooldown_seconds 秒，隔离期内跳过；note_success 把失败数与隔离时刻清零；全部隔离时 active_upstream 返回 None。④resolve 先查缓存，未过期的命中直接返回并把 cached 置真，不得再调 fetcher；同一个 (name, qtype) 的并发查询只能真正回源一次，其余等待者复用同一份结果。⑤应答必须核对 txid 与问题名、问题类型，三者任一不符分别按 DNS_TXID_MISMATCH、DNS_QUESTION_MISMATCH 拒绝，不得写入缓存。⑥NOERROR 的答案必须从问题名开始形成链：CNAME 逐跳跟随，记录属主必须等于当前链尾，出现链外属主记 DNS_BAILIWICK_VIOLATION；记录类型既不是 CNAME 也不等于查询类型记 DNS_ANSWER_TYPE_MISMATCH；空答案记 DNS_RESPONSE_INVALID，整条应答丢弃。⑦正缓存 TTL 逐条钳到 min_ttl 与 max_ttl 之间，原值 0 抬到 min_ttl；一条链的缓存有效期取钳制后各条 TTL 的最小值。NXDOMAIN 只接受空答案，负缓存 TTL 取 negative_ttl 与 max_negative_ttl 的较小值且至少 1 秒，响应缺该字段时用 max_negative_ttl。⑧缓存键是规范化后的 (name, qtype)，同名不同类型各存一份；now 达到 expires_at 即视为过期，lookup 只在严格未过期时返回，且读取不计入 hits、misses，但仍要刷新 LRU 次序。⑨缓存条目数硬顶 max_entries，插入新键且已满时淘汰最久未使用的条目并累加 evictions；purge 删除所有已过期条目并返回条数。⑩任一上游抛出异常或返回被拒应答都记一次失败并轮到下一台，全部尝试失败时，若该键有刚过期且仍在 stale_grace 内的条目，返回 stale 副本并把 stale 置真，否则抛 DNS_RESOLVE_FAILED，detail 的 attempts 按尝试顺序列出上游与错误码；一开始就没有可用上游或全在隔离期抛 DNS_NO_UPSTREAM。⑪被拒的污染应答不得替换或删除该键已有的缓存条目，即使那条已经过期，也要保留给应急回源使用。⑫snapshot 返回深拷贝，含缓存、上游列表、健康状态与九项计数；restore 结构不符抛 DNS_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 queries、hits、misses、stored、poisoned、failures、evictions、purged、stale_served。

硬约束：缓存查找、回源、写回与 LRU 淘汰在同一把可重入锁内完成，同键并发只能产生一次回源，且任意时刻缓存条目数不得超过 max_entries；命中路径不得因为刷新次序而丢条目。快照与恢复整体替换缓存和健康表，恢复失败不能留下半套状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: dns_cache_guard
- typeName: DnsCacheGuard
