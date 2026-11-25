# batch49-py-fix-b-06

## 题面

季度合规检查时，内审抽了十条加密 DNS 查询做复核，发现有两条应答根本对不上问题：一条问的是内网域名，回来的却是另一个域的地址；另一条同一个域名在两个解析器上给出完全不同的结果，而终端侧只看先到的那一条就写进了本地缓存。更棘手的是，其中一台解析器的 HTTPS 证书指纹和半年前登记的记录不一致，没人能说清中间是不是被换过。安全组的诉求是把可信判定收拢到本地：每个应答必须和发出去的问题严格绑定，多个解析器要形成一致结论，缓存只接受通过校验的答案。入口在 gsb/batch49-py-fix-b-06 的 doh_response_verifier.py，类 DohResponseVerifier，负责解析器登记、应答校验、共识判定与缓存准入。

公开入口：DohResponseVerifier(*, min_ttl=30, max_ttl=3600, quorum=2, max_resolvers=8, max_cache=128, chain_depth=8)；register_resolver(resolver_id, endpoint, *, spki_sha256, now=0)；retire_resolver(resolver_id, *, now=0)；resolvers()；begin_query(name, qtype, *, nonce, now=0)；submit(query_id, resolver_id, response, *, now=0)；resolve(query_id, *, now=0)；cache_get(name, qtype, *, now=0)；flush(*, now=0)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 DohVerifyError(code, detail)。

要落地的行为：①解析器登记要求标识非空、endpoint 以 https:// 开头、spki_sha256 是 64 位小写十六进制；重复登记给 DOH_RESOLVER_EXISTS，超过 max_resolvers 给 DOH_CAPACITY，非法字段给 DOH_RESOLVER_INVALID；retire 对未知标识给 DOH_RESOLVER_UNKNOWN，重复退役返回 False。②begin_query 把域名转小写并去掉末尾的点，qtype 只接受 A、AAAA、TXT 且统一大写，nonce 非空且全局唯一，查询号从 q-1 单调递增；空域名、未知 qtype、空 nonce 给 DOH_QUERY_INVALID，nonce 复用给 DOH_NONCE_REUSED。③submit 对未知查询给 DOH_QUERY_UNKNOWN，对未登记解析器给 DOH_RESOLVER_UNKNOWN，对已退役解析器给 DOH_RESOLVER_RETIRED。④应答必须是对象且含 status、content_type、question、answers；content_type 不是 application/dns-json 给 DOH_CONTENT_TYPE；question 的域名与类型必须与查询逐字一致，否则 DOH_QUESTION_MISMATCH。⑤answers 的每条记录必须有 name、type、ttl、data，ttl 必须是 0 以上的整数否则 DOH_TTL_INVALID；记录类型只能是查询类型或 CNAME，且必须沿着 CNAME 链最终落到查询类型的一条记录上，链断给 DOH_CHAIN_BROKEN。⑥CNAME 跳数不得超过 chain_depth，超过给 DOH_CHAIN_TOO_DEEP；链上出现与查询无关的域名给 DOH_QUESTION_MISMATCH。⑦有效 TTL 钳制在 min_ttl 与 max_ttl 之间，单条记录与整次提交返回的 ttl 都取钳制后的最小值。⑧共识按不同解析器的投票分组：同一解析器重复提交同一应答返回 duplicate 且只算一票，同一解析器第二次给出不同应答给 DOH_RESOLVER_CONFLICT 并保留首次票；有效票数少于 quorum 时 resolve 给 DOH_QUORUM_UNMET。⑨票数最多的组达到 quorum 即为结论，票数相同时 AD 标记为真的组优先，仍然相同则给 DOH_CONFLICT；resolve 结果要带 name、qtype、answers、ttl、negative、votes、ad。⑩status 为 3 的 NXDOMAIN 按否定应答处理，answers 必须为空，ttl 取 soa_ttl，缺失时取 min_ttl；status 为 2 的 SERVFAIL 给 DOH_UPSTREAM_FAILURE 且不计票。⑪缓存键按规范化域名与 qtype 生成，写入时记录 now 加 ttl 的到期时间；cache_get 命中过期条目先删除再返回 None，容量超过 max_cache 时淘汰最早写入的条目，flush 删除全部条目并返回条数。⑫snapshot 深拷贝查询表、解析器、缓存与计数；restore 结构不符给 DOH_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 queries、submissions、rejected、resolved、cache_writes、cache_hits、cache_evictions。

硬约束：submit 的校验、计票与 resolve 的分组、缓存写入必须在同一把可重入锁内完成，同一查询的票一旦落下不得被后续提交改写，多个线程同时 resolve 同一查询只能有一次缓存写入，cache_writes 只加一。快照与恢复整体替换查询、解析器、缓存与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后新增的查询与缓存条目都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: doh_response_verifier
- typeName: DohResponseVerifier
