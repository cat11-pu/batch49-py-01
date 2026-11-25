# batch49-py-fix-a-07

## 题面

合规检查把审计证据链列为必查项之后，值班组才发现导出记录经不起推敲：一段日志中间少了两条，序号却仍然连续；有人把某条记录的操作者改掉，重新导出后校验照样显示通过；归档包在迁移时被截短，新的归档又直接盖在缺口的链上，事后无法证明哪一段是原始数据。签名密钥多年没轮换，旧材料与当前活动密钥混在一起，出问题时也说不清某条记录到底由哪把钥匙签的。问题落在 gsb/batch49-py-fix-a-07 的 audit_sig_chain.py，类 AuditSignatureChain，负责逐条签名、链式校验与归档封存。

公开入口：AuditSignatureChain(*, store=None, keys=None, active_key="k1", max_entries=4096, segment_size=8)；append(actor, action, payload, *, now=0)；entry(seq)；head()；verify(*, start=1, end=None, expected_entries=None)；rotate_key(key_id, material, *, make_active=True)；seal_archive(name, *, upto=None)；verify_archive(name, *, live=None)；detect_truncation(*, expected_entries=None)；confirm_head(count, sig=None)；archives()；snapshot()；restore(state)；stats()。拒绝统一抛 AuditChainError(code, detail)。

要落地的行为：①append 的序号从 1 开始连续递增，返回 seq、key_id、actor、action、payload、payload_digest、now、prev_sig、sig；首条 prev_sig 为 64 个零，head 在空链上返回 seq 0 与同样的零签名。②payload 必须是键全为字符串的字典，按 sort_keys 与紧凑分隔符序列化成 UTF-8，payload_digest 取该文本的 SHA-256；sig 取 HMAC-SHA256，消息按 seq、key_id、actor、action、payload_digest、now、prev_sig 的顺序以换行连接，密钥材料按条目的 key_id 查表。③actor 或 action 空白抛 AUDIT_FIELD_INVALID，payload 不是字典、含非字符串键或不可序列化抛 AUDIT_PAYLOAD_INVALID，条数达到 max_entries 抛 AUDIT_CHAIN_FULL；这些失败都不得写入半条记录。④verify 从 1 开始检查序号连续、prev_sig 衔接、key_id 可查、payload_digest 与 sig 重算一致，返回 ok、checked、first_bad、code、head；序号断裂记 AUDIT_SEQ_GAP，衔接不符记 AUDIT_CHAIN_BROKEN，密钥缺失记 AUDIT_KEY_UNKNOWN，重算不符记 AUDIT_SIGNATURE_MISMATCH。⑤篡改第 k 条时 first_bad 必须是 k，且 end 小于 k 的子区间校验仍应通过。⑥删除中间条目后，无论从 1 还是从被删序号起始校验，都要把缺口定在被删的序号上。⑦rotate_key 新增密钥并默认切为活动密钥，返回新增数量；同编号换材料抛 AUDIT_KEY_EXISTS，同编号同材料只切换活动密钥并返回 0；旧条目继续用各自 key_id 验签。⑧seal_archive 对 1 到 upto 的条目按 segment_size 切段，每段记录 from_seq、to_seq 与段内各 sig 拼接后的 SHA-256，清单还带 name、upto_seq、count、head_sig；同名同 upto 重复封存返回原清单，同名改短抛 AUDIT_ARCHIVE_CONFLICT。⑨verify_archive 重算每段摘要并复验区间签名，链被改、被截短或段摘要不符都返回 ok 为 False、first_bad 指向首个坏点、code 记 AUDIT_ARCHIVE_MISMATCH；未知归档名抛 AUDIT_ARCHIVE_UNKNOWN。⑩detect_truncation 以已归档水位与 expected_entries 的较大值为基准，尾部缺失记 tail，中段缺号记 gap，首条序号不为 1 记 head；已被截到低于归档水位的链不得再以新名字封存，必须抛 AUDIT_CHAIN_TRUNCATED。⑪confirm_head 用外部回执抬高已知头部，水位回退抛 AUDIT_HEAD_REGRESSION，同高度换签名抛 AUDIT_HEAD_CONFLICT；此后若该高度的签名被替换，detect_truncation 要报 head_replaced。⑫entry、head、archives 与 snapshot 都返回深拷贝，调用方就地修改不得影响链内数据；restore 要求结构完整，失败抛 AUDIT_SNAPSHOT_INVALID 且原状态继续可用。

硬约束：append 在同一把可重入锁里完成取号、取前序签名、计算与落盘，多个写入方并发时序号不得重复或跳号，并在全部完成后仍可整链验签通过；归档封存与读写共用锁，封存出的清单必须对应某一确定前缀，不能混入封存后才写入的条目。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: audit_sig_chain
- typeName: AuditSignatureChain
