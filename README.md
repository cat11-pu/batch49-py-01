# batch49-py-safety-b-12

## 题面

漏洞验证平台这周被安全运营连着投诉两次。第一次是一条早就判过不可利用的漏洞，靶标重建镜像之后端口从 80 变成 8080，平台仍然拿几十天前的负结果直接回结论，真正能打的那台机器被漏掉。第二次是同一漏洞的八个查询几乎同时打进来，平台起了八份验证脚本，把靶标池占满，其中三份脚本自己报错退出，系统却把它们记成不可利用，报告直接发给了业务方。运维还发现验证结束的靶标有时留在池子里没人回收，下一次验证直接复用了带残留状态的靶标，结论同样不可信。现在要求平台把靶标租约、三态结论、重试上限和负结果缓存这几件事收拢到一个组件里。

改动范围限定在 gsb/batch49-py-safety-b-12 的 exploit_verification_orchestrator.py，类型 ExploitVerificationOrchestrator。对外接口：ExploitVerificationOrchestrator(*, max_retries=2, verdict_ttl=600, lease_ttl=120)；register_target(target)；fingerprint(target_id)；acquire_lease(target_id, now)；release_lease(lease_id)；active_leases()；verify(vulnerability_id, target_id, runner, now)；verdict_for(vulnerability_id, target_id, now)；invalidate(vulnerability_id, target_id)；snapshot()；restore(state)；stats()。Target 用 dataclass 承载 target_id、image_digest、ports；TargetLease 带 lease_id、target_id、fingerprint、expires_at；VerificationVerdict 带 vulnerability_id、target_id、status、attempts、evidence_ref、cached。

要修的点：

① 靶标按 target_id 唯一，重复登记报 TARGET_DUPLICATE，空 id 或空镜像摘要报 TARGET_INVALID。
② fingerprint 由镜像摘要与端口集合算出，同一靶标多次调用一致，摘要或端口变化结果就变。
③ 租约到期时刻等于 now 加 lease_ttl，租约里带上当时的靶标指纹。
④ 同一靶标已有未到期租约时报 LEASE_BUSY，detail 带靶标号；now 不早于 expires_at 即算到期，先作废再发新租约，旧租约号此后不可释放。
⑤ 释放不存在或已作废的租约返回 False 而不报错；active_leases 按 target_id 升序。
⑥ 结论只有 exploitable、not_exploitable、inconclusive 三种，脚本返回可利用立刻收手，attempts 记真实执行次数。
⑦ 脚本返回 uncertain 一类不确定态按重试处理，最多 1 加 max_retries 次，用尽落 inconclusive。
⑧ 脚本自身抛异常不得记成不可利用，只能算不确定并计入重试。
⑨ 不可利用结论按（漏洞、靶标）缓存 verdict_ttl 秒，窗口内重复验证直接返回 cached 为真的同一结论且不再执行脚本；可利用结论不缓存，缓存条数按靶标数封顶，不随验证次数增长。
⑩ 缓存项绑定执行当时的靶标指纹，指纹变化后旧条目必须失效。
⑪ 同一（漏洞、靶标）的并发验证要合并成一次执行，被合并进来的请求拿到同一结论与同一 evidence_ref。
⑫ verify 无论成功、重试耗尽还是脚本报错都必须释放租约，遗留租约不得被下一次验证复用；脚本返回无法识别的字符串报 RUNNER_RESULT_INVALID。

硬约束：租约的发放与回收是多协程并发入口，同一靶标同时只允许一张有效租约；验证执行次数受重试上限硬顶，不为了一次失败无限重跑；负结果缓存与租约状态要能快照并在新进程里恢复，恢复后缓存、靶标指纹和计数都还在。stats 返回 targets、verifications、cache_hits、leases_issued 四个累计计数，verifications 只统计真正执行了脚本的次数。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，不改测试、不跳过、不按固定输入写死；并发验证与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: exploit_verification_orchestrator
- typeName: ExploitVerificationOrchestrator
