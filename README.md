# batch49-py-safety-d-05

## 题面

域名解析侧连续三晚出现同一种抱怨：明明记录存在，用户却偶尔拿到 NXDOMAIN，而且第二次查又好了；被黑洞的上游一直不返回包，解析器却死等它，正常的权威响应在队列里排队超时；还有一次 UDP 应答被截断，代码直接把半截记录当完整结果返回，下游负载均衡拿它做了灰度切换。现在要把多上游竞速收成一个统一仲裁器，落在 gsb/batch49-py-safety-d-05 的 resolver_race_arbiter.py，类 ResolverRaceArbiter，外部只注入一个 responder(upstream, query, transport) 用于取应答。

公开接口：ResolverRaceArbiter(responder, *, max_inflight_per_upstream=2, penalty_threshold=2, cooldown_seconds=10, max_upstreams=8)；set_upstreams(upstreams) 返回注册数量；upstreams()；resolve(query, *, now) 返回 query、status、answers、upstream、rcode、transport 六字段；tcp_retry(query, upstream, *, now)；health(upstream=None, *, now=None)；result_of(query)；deliver_late(query, upstream, response, *, now)；stats；snapshot；restore。拒绝统一抛 ResolveError(code, detail)。responder 返回 rcode、answers、truncated、latency_ms 四个字段。

要落地的故障与边界：①查询去空白后不得为空，空查询抛 RESOLVE_QUERY_INVALID；上游列表必须非空且元素为非空字符串，否则抛 RESOLVE_UPSTREAM_INVALID，数量超过 max_upstreams 抛 RESOLVE_UPSTREAM_LIMIT，任何失败都不得改动已注册列表；②set_upstreams 整体替换现有上游并返回数量，upstreams() 按注册顺序返回；③同一查询只真正解析一次，再次调用直接返回已有结果的副本并累加 cache_hits，不得再调用 responder；④所有候选上游各发一次请求，latency_ms 最小者胜出，延迟相同按注册顺序靠前者胜出，落败应答全部取消并计入 cancelled；⑤rcode 为 0 记 ok，为 3 记 nxdomain 且 answers 返回空元组，为 2 记 servfail 并累加该上游失败数，失败数达到 penalty_threshold 后进入冷却，冷却截止为当前时刻加 cooldown_seconds，health 能查到状态、在途数、失败数与冷却截止；⑥每个上游在途请求不得超过 max_inflight_per_upstream，无论成功失败，resolve 返回后所有上游在途数必须归零；⑦首个应答是 NXDOMAIN 而另一上游随后返回记录时，必须以 NXDOMAIN 为准并取消其余，不得采信较晚的正向应答；⑧应答被截断时必须回退 TCP 重查，tcp_retry 强制走 tcp 传输并返回该次结果，未解析过的查询抛 RESOLVE_QUERY_UNKNOWN，未注册的上游抛 RESOLVE_UPSTREAM_UNKNOWN；⑨TCP 重查结果仍然截断时按解析失败返回，不能把截断数据当成完整结果；⑩上游处于惩罚冷却时不得被选为主发，只有全部上游都在冷却时才允许拿它做最后一次兜底，并计入兜底次数；⑪迟到应答由 deliver_late 交给仲裁器，一律丢弃并返回假，不得改写已存结果；⑫结果按查询、健康按上游分别保存，不同查询之间互不影响，deliver_late 收到从未解析过的查询也只计数不建结果。

stats 固定返回 resolved、nxdomain、servfail、cancelled、cache_hits、late_dropped、fallback、penalized、tcp_retries 九个计数；health 传单个上游时返回 upstream、state、inflight、failures、cooldown_until 五个字段，不传上游时返回以上游名为键的字典；snapshot 固定返回 version、upstreams、results、counters 四个键，除 version 与计数外都是可变副本，调用方清空或改写它们不得影响内部状态，restore 遇到非字典、版本不符、键缺失或类型不对一律抛 RESOLVE_SNAPSHOT_INVALID 且现场状态不变。

硬约束：并发解析时在途计数的加减必须在锁内完成，八路并发解析不同查询后所有上游在途数必须回到零，不得泄漏名额；同一查询的重复解析只允许产生一份结果，后到者只能读缓存；快照深拷贝，恢复后上游列表、已存结果、失败与冷却计数完整且不与原对象共享结构，重启后冷却中的上游不得被当成健康节点。

验收：python3 -m pytest -q tests 的 15 条用例必须全过；不得改测试、不得跳过、不得按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: resolver_race_arbiter
- typeName: ResolverRaceArbiter
