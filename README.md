# batch49-py-fix-b-04

## 题面

季度漏洞通报会上，两份材料对不上：扫描平台说某个 CVE 的基础分是 9.8，资产台账按同一份评分表算出来却是 6.1；再查，评分表两周前被人改过一个权重，改动没有留签名，也没人能证明哪版是原始表。更麻烦的是，同一条 CVE 的修订号被人从 3 改回 2，旧结论重新盖在新结论上，通报邮件已经发出。现在的诉求很直接：分数必须能在本地按向量重算，评分表要有签名且改动能被发现，每条结论都要能追溯到签名者和修订号。入口在 gsb/batch49-py-fix-b-04 的 cvss_score_ledger.py，类 CvssScoreLedger，负责签名校验、权重表完整性、CVSS 3.1 基础分重算与修订台账。

公开入口：CvssScoreLedger(*, max_entries=128, max_keys=4, max_revisions=8)；register_key(key_id, secret, *, break_glass=False)；revoke_key(key_id)；install_table(table, *, key_id, signature)；audit_table()；override_weight(metric, level, value, *, key_id, signature, now=0)；compute_score(vector)；severity(score)；ingest(cve, vector, score, *, revised, key_id, signature, now=0)；get(cve)；history(cve)；quarantined()；snapshot()；restore(state)；stats()。拒绝统一抛 CvssScoreError(code, detail)。

要落地的行为：①向量必须以 CVSS:3.1/ 开头，AV、AC、PR、UI、S、C、I、A 八个基础度量各出现一次，取值必须是已安装权重表里该度量下存在的等级；缺项、重复、未知度量、取值非法一律给 CVSS_VECTOR_INVALID。②基础分按 CVSS 3.1 公式计算：ISS 为 1 减三项影响权重的连乘，Impact 在 S:U 时取 6.42 乘 ISS、S:C 时取 7.52 乘 ISS 减 0.029 再减 3.25 乘 ISS 减 0.02 的 15 次方，Exploitability 为 8.22 乘 AV、AC、PR、UI（PR 按 Scope 取 PR_U 或 PR_C），Impact 为 0 时记 0 分，S:U 取 Impact 加 Exploitability 与 10 的较小值，S:C 取 1.08 倍 Impact 加 Exploitability 与 10 的较小值，最后向上取整到一位小数。③severity 分档：0.0 为 None，0.1 到 3.9 为 Low，4.0 到 6.9 为 Medium，7.0 到 8.9 为 High，9.0 到 10.0 为 Critical。④密钥登记：key_id 非空、secret 至少 16 个字符；重复登记给 CVSS_KEY_EXISTS，超过 max_keys 给 CVSS_CAPACITY_EXCEEDED，register_key 返回 key_id 与 break_glass；吊销幂等，用已吊销密钥签出的 bundle 一律 CVSS_SIGNATURE_INVALID。⑤权重表安装：表必须含 AV、AC、PR_U、PR_C、UI、C、I、A 八个度量且每个度量的等级齐全；签名是 HMAC-SHA256(secret, 签名域加规范化 JSON) 的小写十六进制，表的签名域为 table:，规范化 JSON 即键排序、无空格分隔的 UTF-8 序列；先校验表结构再验签名，表结构不完整给 CVSS_TABLE_INVALID，签名不符给 CVSS_SIGNATURE_INVALID，install_table 返回 digest 与 metrics。⑥audit_table 返回 ok、drifted 与 digest；表未安装给 CVSS_TABLE_MISSING；漂移未修复期间 ingest 一律给 CVSS_TABLE_DRIFT。⑦紧急改权重只有 break_glass 密钥可以签，签名域为 override: 加规范化后的 metric、level、value；非 break_glass 给 CVSS_OVERRIDE_DENIED；成功时返回 metric、level、value 与 drifted，审计报漂移、发布挂起，重装表才恢复。⑧摄取签名域为 bundle: 加规范化后的 cve、vector、score、revised，CVE 必须形如 CVE-年份-编号；未知或已吊销密钥、签名不符一律 CVSS_SIGNATURE_INVALID，且不落任何记录、不推进修订；bundle 里任一字段被改动，包括只改 revised，都必须验签失败，get 返回 cve、vector、score、severity、revised 与 key_id。⑨声明分数必须是 0 到 10 的一位小数，按当前权重表重算并向上取整后不等就给 CVSS_SCORE_MISMATCH，detail 给出 claimed 与 computed，该 CVE 进入隔离列表；隔离只拒绝本次提交，已发布的旧版本仍可读。⑩revised 必须是正整数且严格大于该 CVE 已接受的最大修订，否则给 CVSS_REVISION_STALE；接受后 history 按修订升序，超过 max_revisions 时挤掉最旧一版并计入 superseded。⑪同 cve、同 revised、同签名的重复摄取返回 duplicate 且不重复计数，首次摄取返回的 status 为 published；新 CVE 使条目数超过 max_entries 给 CVSS_CAPACITY_EXCEEDED 且不留下半条记录。⑫快照深拷贝密钥、权重表、记录、隔离列表与计数，返回 keys、table、entries、quarantined、stats 五个键；restore 结构不符给 CVSS_SNAPSHOT_INVALID 且原状态可用；stats 至少含 ingested、published、duplicates、quarantined、signature_failures、stale_revisions、superseded、overrides、entries；漂移未修复时反复 audit_table 必须持续报告同一组 drifted 度量，不能因重复读取自愈。

硬约束：验签、重算、修订判定与落库必须在同一把可重入锁内完成，任何一步失败都不得留下记录、隔离项或计数增量，同一个 bundle 并发提交只能发布一次。快照与恢复整体替换密钥、权重表、记录与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后写入的结论与计数都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: cvss_score_ledger
- typeName: CvssScoreLedger
