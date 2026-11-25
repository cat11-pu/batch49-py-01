# batch49-py-fix-c-01

## 题面

边缘防护节点的 WAF 规则长期靠手工拷贝，上一次上线把一条误报很高的规则推到了全部机房，值班只能逐台改回去；同一周另一批规则包传到第三个机房时签名对不上，执行脚本照样覆盖，旧规则被改成了半套。复盘还发现，灰度比例只写在发布单上，没有任何代码按比例分流；某次回滚只退回一半分片，另外一半继续用新规则跑了两小时。现在要把规则发布收口成一个带签名链、灰度分片与原子回滚的协调器，入口在 gsb/batch49-py-fix-c-01 的 waf_rule_release_guard.py，类 WafRuleReleaseGuard，分片数固定为 20，编号 0 到 19。

公开入口：WafRuleReleaseGuard(*, max_rules=64, max_batches=6, max_pins=8)；trust_key(key_id, material)；untrust_key(key_id)；verify_release(release)；submit(release, *, request_id, now=0)；promote(request_id, *, percent, now=0)；commit(request_id, *, now=0)；rollback(request_id, *, now=0)；shard_ruleset(shard_id)；active_ruleset()；release_status(request_id)；pin(*, now=0)；unpin(token)；snapshot()；restore(state)；stats()。拒绝统一抛 WafRuleReleaseError(code, detail)。

要落地的行为：①发布包是含 release_id、key_id 与 batches 的字典，batches 非空；每批含 batch_id、percent、ops、prev_digest 与 signature，batch_id 不得重复，percent 为 5 的倍数且落在 5 到 100 之间，各批 percent 不得下降且最后一批必须是 100，结构不合法给 RELEASE_INVALID，且不留下任何请求记录。②trust_key 的 material 至少 16 个字符，否则 RELEASE_KEY_INVALID，重复登记同一 key_id 覆盖旧材料；untrust_key 只对已登记键返回 True。③校验顺序固定为结构、限额、密钥、签名链、规则编译。签名正文是 {"release_id","batch_id","percent","prev_digest","ops"} 的紧凑排序 JSON，signature 为 key_id 对应材料的 HMAC-SHA256 小写十六进制，ops 顺序参与签名；首批 prev_digest 为 64 个 0，之后每批的 prev_digest 必须等于前一批正文的 SHA-256 小写十六进制摘要，链断给 RELEASE_CHAIN_BROKEN。未知 key_id 给 RELEASE_KEY_UNKNOWN，摘要不符给 RELEASE_SIGNATURE_INVALID。④ops 只能取 add、update、remove：add 的 rule_id 不得已在规则集里，update 与 remove 必须命中已有 rule_id；每条规则要有非空 pattern、action 取 block 或 log、priority 为 1 到 100 的整数；按批顺序编译，任何一条不合法整包拒绝并给 RELEASE_RULESET_INVALID，不得留下半套候选规则。⑤编译后规则数超过 max_rules 或批数超过 max_batches 给 RELEASE_LIMIT_EXCEEDED；规则对外按 priority 降序、rule_id 升序排列。⑥submit 成功把整包登记为候选，返回 release_id、status、percent 0、generation 与 rules；同一 request_id 重放同一内容返回首次结果并标 replayed，不得重复登记；同 request_id 换内容给 RELEASE_REQUEST_CONFLICT，已有未终态发布时再登记新发布给 RELEASE_BUSY。⑦promote 的 percent 必须是 5 的倍数、不小于当前比例且不超过 100，否则 RELEASE_PERCENT_INVALID；比例为 p 时覆盖编号小于 p 除以 5 的分片，返回 percent、generation 与覆盖分片列表；覆盖分片从 shard_ruleset 读到候选规则与 generation 加一，未覆盖分片继续读到旧规则。⑧commit 只在比例到 100 时成功，否则 RELEASE_NOT_READY；成功后候选转正，generation 加一，20 个分片同时切到新规则，active_ruleset 返回同一代内容，release_status 转 committed。⑨rollback 对已提交的发布把规则集整体退回提交前内容并让 generation 再加一，对只登记或只灰度的发布直接丢弃候选、generation 不变；重复回滚返回首次结果、generation 不再变化，未知请求给 RELEASE_REQUEST_UNKNOWN。⑩已提交或已回滚的请求再 promote 或 commit 给 RELEASE_STATE_INVALID。⑪pin 返回当前代的只读视图与令牌，pin 数达到 max_pins 给 RELEASE_PIN_LIMIT，pin 视图在后续提交与回滚后仍按签出时的内容服务；unpin 未知令牌返回 False。⑫snapshot 深拷贝密钥、稳定规则集、候选、请求台账、pin、generation 与计数；restore 结构不符给 RELEASE_SNAPSHOT_INVALID 且原状态可用，恢复后 generation 与计数从快照值继续推进，重放同一 request_id 不得重新登记，灰度中的候选可以继续推进。

硬约束：verify、submit、promote、commit、rollback 与 pin 在同一把可重入锁内串行，多个调用方并发提交同一 request_id 时只能有一个登记成功，其余必须拿到同一份首次结果；提交与回滚整包生效，任何分片不得读到一半新一半旧的规则。快照恢复整体替换全部状态，失败不得留下半套；max_rules、max_batches、max_pins 都是硬顶，超限一律拒绝而不是挤掉已有条目。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: waf_rule_release_guard
- typeName: WafRuleReleaseGuard
