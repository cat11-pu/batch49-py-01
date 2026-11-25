# batch49-py-safety-b-20

## 题面

跨集群镜像签名验证上线后，证明链在中间一环断掉时整体只报一句签名无效，值班同学拿着单子不知道是缺环、密钥轮换没接上还是签名被改；密钥轮换当天，旧密钥在轮换窗口外签出的信封仍然通过，新密钥反而因为窗口没衔接被判失败；更隐蔽的是某个信封在链里出现两次，验证器沿着它转了一圈才超时，整条流水线被拖死。范围是 gsb/batch49-py-safety-b-20 的 attestation_chain_verifier.py，类型 AttestationChainVerifier，负责多环证明信封的逐环锚定、轮换衔接与断链定位。

公开接口（签名不变）：AttestationChainVerifier(*, overlap_seconds=300, max_chain=32)；add_root(key_id, *, owner, secret, not_before, not_after)；rotate_key(old_key_id, new_key_id, *, at, secret)；active_keys(at)；verify_envelope(envelope, *, at) 返回 RingResult；verify(chain, *, at) 返回 ChainVerdict；locate_break(chain, *, at)；root_version()；history()；snapshot()；restore(state)；stats()；模块级 sign_envelope(payload_sha, key_id, secret)。拒绝时抛 AttestationError(code, detail)。

要修的点：①信任根注册校验 key 与 owner 非空、生效窗口不反序，重复 key 拒绝，每次成功注册推进根版本；②密钥轮换从旧密钥派生新密钥，继承 owner，新旧密钥并存一段重叠窗口，轮换同样推进根版本，active_keys 按 key id 升序返回窗口内的密钥；③sign_envelope 用 payload、key_id 与密钥材料算出稳定的六十四位十六进制摘要，构造与验证共用同一算法；④链首信封必须由已注册的信任根或轮换派生密钥签发，签发者要等于该密钥的 owner；⑤除首环外，每环的 issuer 必须等于上一环的 subject，断链要定位到具体环节；⑥逐环验签，签名不符、密钥不存在、密钥在签名时刻不在有效窗口要给出各自的原因码，不能一律报签名无效；⑦轮换窗口内新旧密钥都能验，超出窗口后旧密钥必须拒绝；⑧链中间某环缺失（seq 跳号）要报缺失位置，不能报成签名无效；⑨同一信封在链中重复出现要识别为环并拒绝，不能无限展开；⑩断链只报第一处失败，给出 index、envelope_id 与原因，成功时每环结果都要带 key_id、issuer、subject；⑪链长度有硬上限，超限直接拒绝；⑫信任根配置带版本，验证结论记录所用根版本与验证时刻，历史按调用顺序保留。

硬约束：verify 会被多个流水线线程并发调用，历史与计数逐次追加不能交错，根版本与密钥表不能半更新；locate_break 只做定位，不写历史也不计验证次数；超过长度上限或链结构非法时抛错且不改变任何状态；stats 固定返回 verifications、verified、rejected、rings 四个计数，rings 按实际求值的环数累加；快照恢复后密钥表、根版本、历史与计数都要完整还原，恢复失败的快照不得改动现有状态。

验收：python3 -m pytest -q tests 15 条必须全过，不改测试、不跳过、不按固定输入写死；并发验证与快照恢复自行验证；只做本地实现，交付源码与运行记录。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: attestation_chain_verifier
- typeName: AttestationChainVerifier
