# batch49-py-safety-c-03

## 题面

资源服务器切到 DPoP 之后，客户端支持群里连着报了三类问题：令牌刷新时偶尔整批 401，抓包看到服务端反复回 use_dpop_nonce，客户端补一次又被打回重放；运维更换签名密钥后，旧钥在宽限期内还能用，可宽限期过了仍有零星请求通过，看起来像迁移动作把截止时间往后推了；还有一次 nonce 表被打满，新客户端直接签不出 nonce，重启后状态也对不上。值班同学翻日志时又发现，同一个 nonce 在两台副本上各成功核销了一次，审计里多出一条无法归属的刷新记录。范围是 gsb/batch49-py-safety-c-03 下的 dpop_nonce_issuer.py，类 DpopNonceIssuer，负责按客户端签发带代际的短期 nonce、校验 proof 归属与令牌的 jkt 绑定。

公开接口：DpopNonceIssuer(*, nonce_capacity=256, nonce_ttl_ms=60000, migration_grace_ms=300000)；register_client(client, *, now=None) -> int；rotate_generation(client, *, now) -> int；issue_nonce(client, *, now) -> Nonce；verify_proof(client, token_id, *, nonce, jkt, now) -> ProofResult；bind_token(client, token_id, *, jkt, now) -> Binding；migrate_key(client, token_id, *, new_jkt, now) -> Binding；expire(now) -> int；binding_of(token_id)；nonce_of(value)；stats() -> dict；snapshot()、restore(state)。now 一律为整数毫秒。拒绝抛 DpopError(code, detail)，码为 DPOP_INVALID_REQUEST、DPOP_UNKNOWN_CLIENT、DPOP_TOKEN_ALREADY_BOUND、DPOP_MIGRATION_USED、DPOP_SNAPSHOT_INVALID。

需求与边界：
①构造参数 nonce_capacity、nonce_ttl_ms 必须为正，migration_grace_ms 不得为负；register_client 空客户端抛 DPOP_INVALID_REQUEST，重复登记保持原代际。
②issue_nonce 给已登记客户端签发 Nonce(value, client, generation, expires_at, consumed)，代际取客户端当前代际，expires_at 为 now 加 nonce_ttl_ms；未知客户端抛 DPOP_UNKNOWN_CLIENT。
③nonce 表受容量上限约束，满时淘汰最早过期的一条而不是拒绝签发，被淘汰的 nonce 再次出现按 unknown_nonce 处理。
④bind_token 要求客户端已登记、token_id 与 jkt 非空，成功返回 Binding(token_id, client, current_jkt, previous_jkt, grace_until, bound_at)；重复绑定同一令牌抛 DPOP_TOKEN_ALREADY_BOUND，失败不留半条记录。
⑤verify_proof 依次判定：客户端登记、令牌归属（unknown_token）、nonce 存在（unknown_nonce）、nonce 归属（foreign_nonce）、nonce 过期（expired_nonce）、代际、jkt 归属（key_mismatch），最后一次性核销并返回 ok。
⑥同一 nonce 只允许核销一次；成功后 consumed 置真，再次提交返回 replay；并发提交同样只能有一个 ok。
⑦jkt 与绑定不符返回 key_mismatch 且不消耗 nonce；unknown_token、foreign_nonce、expired_nonce 同样不消耗。
⑧代际落后于客户端当前代际的未用 nonce，第一次提交回 use_dpop_nonce 并附一条新 nonce，同一旧 nonce 再次提交按 replay 处理，不得放行。
⑨migrate_key 把当前钥切到新钥并记下旧钥，截止 grace_until 为 now 加 migration_grace_ms，宽限期内新旧钥都能通过校验；同一令牌只允许迁移一次，后续迁移抛 DPOP_MIGRATION_USED，任何操作都不得顺延已定的 grace_until。
⑩宽限期之后旧钥签名一律 key_mismatch，即使旧 nonce 仍然有效也不放行。
⑪expire 删除 expires_at 不晚于 now 的 nonce 并返回条数，计入 nonces_expired；stats 固定十项：clients、nonces_issued、nonces_evicted、nonces_expired、verifies、verified、challenged、replays、key_mismatch、migrations。
⑫snapshot 返回 version 以及 clients、bindings、nonces、stats 的深拷贝；restore 先整体校验再落盘，结构不合法抛 DPOP_SNAPSHOT_INVALID 且现场不变。

硬约束：同一 nonce 的核销必须在锁内完成，并发下只能成功一次；并发写入不得出现半更新状态，计数与实际表项始终对得上；nonce 表的容量与过期回收必须有界，不能靠无界增长扛量；快照恢复后代际、绑定、宽限期与计数必须一致。

验收：python3 -m pytest -q tests 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: dpop_nonce_issuer
- typeName: DpopNonceIssuer
