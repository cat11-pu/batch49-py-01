# batch49-py-fix-d-02

## 题面

镜像仓库换了准入链路，审计连开三张单。第一张是某支付镜像只带一个签名就进了生产，策略要求三个签名人至少两个，准入接口没数人数，直接放行；第二张是某签名人密钥下午被撤销，撤销单发出去了，在跑的集群照样拉取，撤销状态没人往下传播；第三张更麻烦，灰度比例只写在发布单上，代码没按比例分流，新镜像一次推到全部命名空间，回滚时找不到上一个稳定摘要。现在要把多签名阈值、密钥轮换与撤销传播、灰度放行收进一个本地准入器，判定结果必须可复算。

入口在 gsb/batch49-py-fix-d-02 的 image_signature_admission.py，类 ImageSignatureAdmission，拒绝统一抛 ImageAdmissionError(code, detail)，视图类型 PolicyView、SignerView、SignatureView、AdmissionDecision。公开方法：ImageSignatureAdmission(*, max_images=64)；register_signer(signer_id, *, key_version=1)；signer_of(signer_id)；rotate_signer(signer_id)；revoke_signer(signer_id, *, at, enforce_after=30)；propagate(now)；set_policy(repository, *, signers, threshold, rollout_percent=0)；policy_of(repository)；submit_signature(repository, digest, signer, *, key_version, at)；signatures_of(repository, digest)；threshold_state(repository, digest)；admit(namespace, repository, digest, *, now)；promote(repository, digest, *, now)；rollback(repository, *, now)；stable_of(repository)；history(repository)；stats()；snapshot()；restore(state)。

要落地的行为：

①register_signer 要求 signer_id 为字符串、去首尾空白后非空，key_version 为不小于 1 的整数，否则 IMAGE_SIGNER_INVALID；活跃同名重复登记给 IMAGE_SIGNER_CONFLICT；序号从 1 起递增，signer_of 未知给 IMAGE_SIGNER_UNKNOWN。已撤销的 signer_id 重新登记按新身份分配新序号，旧签名不复活，撤销记录保留。

②set_policy 要求 repository 非空，signers 是互不重复的非空字符串且 1 到 16 个，threshold 在 1 到名单长度之间，rollout_percent 只能是 0、20、40、60、80、100，否则 IMAGE_POLICY_INVALID，返回值按 signer 升序；名单里的 signer 未登记给 IMAGE_SIGNER_UNKNOWN；policy_of 未登记仓库给 IMAGE_POLICY_UNKNOWN。

③submit_signature 要求仓库已有策略、digest 为 sha256: 加 64 位小写十六进制、signer 已登记且在策略名单内，否则按 IMAGE_POLICY_UNKNOWN、IMAGE_DIGEST_INVALID、IMAGE_SIGNER_UNKNOWN、IMAGE_SIGNER_NOT_ALLOWED 拒绝；已落实撤销的 signer 提交给 IMAGE_SIGNER_REVOKED。

④同一 signer 对同一镜像重复提交且 key_version 不高于已有值返回 False，首次 at 保留；key_version 更高时替换旧签名并返回 True；新签名创建镜像记录，max_images 是镜像记录数硬上限，超限给 IMAGE_CAPACITY_EXCEEDED 且不落记录。

⑤rotate_signer 把 key_version 加一并返回新值，未知给 IMAGE_SIGNER_UNKNOWN，已撤销给 IMAGE_SIGNER_REVOKED，旧版本签名保留但状态转 stale_key，不再计入阈值。revoke_signer 记录 revoked_at 与 enforce_at 等于 at 加 enforce_after，enforce_after 为负给 IMAGE_SIGNER_INVALID，未知给 IMAGE_SIGNER_UNKNOWN，重复撤销返回 False；撤销未落实的签名仍计入阈值，同时在报告里计 tainted。

⑥propagate(now) 把 enforce_at 不晚于 now 的撤销落实，返回按 signer_id 升序的元组；已落实的签名不再计入 valid 改计 revoked，generation 递增；多个 signer 同时到期必须按 signer_id 升序一次落实，generation 只递增一代。

⑦signatures_of 按 signer 升序返回 SignatureView，state 优先级为 revoked、pending_revocation、stale_key、valid；threshold_state 返回 required、valid、missing、stale、revoked、tainted、met 七项，valid 只数 key_version 与当前一致且未落实撤销的签名，missing 是策略名单里缺有效签名的 signer 升序元组，未登记镜像返回零报告。

⑧admit 对空 namespace 给 IMAGE_NAMESPACE_INVALID，未知仓库给 IMAGE_POLICY_UNKNOWN，digest 非法给 IMAGE_DIGEST_INVALID；镜像只要带一条已落实撤销的签名就拒绝，理由 signer_revoked；valid 少于 threshold 拒绝，理由 threshold_not_met。

⑨灰度桶只由 namespace 决定，取 sha256(namespace) 十六进制前 8 位转整数对 100 取模，与 repository、digest 无关；digest 等于 stable 摘要时直接放行，模式 stable；否则 bucket 小于 rollout_percent 放行为 canary，不小于则拒绝，理由 rollout_hold。

⑩准入决策按 namespace、repository、digest 与 generation 缓存：同一代重复调用返回同一决策并计 cached_hits，不重复计 evaluations，decided_at 保持首次值；set_policy、submit_signature、rotate_signer、revoke_signer、propagate、promote、rollback 都令 generation 递增，旧代缓存不得复用。

⑪promote 要求阈值满足且没有已落实撤销的签名，否则给 IMAGE_THRESHOLD_NOT_MET 或 IMAGE_SIGNER_REVOKED；成功写入 stable 与历史返回 True，已是同一摘要返回 False；rollback 弹出最近稳定摘要恢复上一个，没有上一个给 IMAGE_NO_PREVIOUS_STABLE；stable_of 与 history 对未登记仓库返回 None 与空元组。

⑫stats 固定返回 evaluations、admitted、denied、cached_hits、promotions、rollbacks、revocations、enforced 八项；snapshot 返回 version、policies、signers、images、stable、history、generation、decisions、counters，version 固定为 1，必须深拷贝；restore 结构不符给 IMAGE_SNAPSHOT_INVALID 且当前状态不变。

硬约束：admit 的评估、写回与计数必须在同一把可重入锁内完成，同一代并发准入只评估一次，其余命中缓存；快照与恢复整体替换策略、签名、镜像、稳定摘要、历史、代际、决策与计数，恢复失败不得留下半套状态；max_images 是硬顶，签名写入不得绕过。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或按固定输入写死；并发与重启场景自行验证；只做本地实现，只用标准库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: image_signature_admission
- typeName: ImageSignatureAdmission
