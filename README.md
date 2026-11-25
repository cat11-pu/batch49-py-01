# batch49-py-safety-b-13

## 题面

上一轮利用验证报告出了两个笑话。一台目标被判成可利用，复核时发现阈值要求三个变体成功，实际只打通一条载荷，聚合环节把同一变体重试三次的成功数重复计了三遍；另一台目标的所有变体都因镜像指纹不匹配被筛掉，报告却写成不可利用，工单差点被关掉。还有一批目标跑到第七条载荷时执行预算就见底了，报告仍然标着覆盖完整。安全团队要求把载荷矩阵的筛选、阈值、预算和明细全部收进一个组件，报告口径必须能复核。可现在的明细里只有一个总数，哪条载荷失败、失败在第几次重试都查不到，复核一次要把整批目标重跑一遍，时间全耗在这上面。

改动范围限定在 gsb/batch49-py-safety-b-13 的 exploit_payload_matrix.py，类型 ExploitPayloadMatrix。对外接口：ExploitPayloadMatrix(*, success_threshold=2, total_budget=10, variant_retries=1)；add_variant(variant)；applicable_variants(fingerprint)；execute(fingerprint, runner)；result_for(fingerprint, variant_id)；successful_count(fingerprint)；budget_remaining(fingerprint)；coverage_complete(fingerprint)；reset_target(fingerprint)；snapshot()；restore(state)；stats()。PayloadVariant 用 dataclass 承载 variant_id、targets、payload；MatrixResult 返回 fingerprint、status、successful_variants、failed_variants、skipped_variants、executed、coverage_complete。runner 接收变体号，返回 success 或 failure。

要修的点：

① 变体按 variant_id 唯一，重复登记报 MATRIX_DUPLICATE，空变体号或空适用目标列表报 MATRIX_INVALID。
② applicable_variants 只返回适用目标里含当前指纹的变体，按变体号升序，不适用的一律不执行。
③ 执行顺序固定按变体号升序，与登记顺序无关。
④ status 四态：exploitable、not_exploitable、inconclusive、unverifiable；成功变体数达到阈值即 exploitable。
⑤ 成功阈值按去重后的成功变体数计算，同一变体重试成功多次只算一个。
⑥ 单个变体失败按 variant_retries 重试，最多 1 加 variant_retries 次，重试同样计入执行次数与预算，成功一次后同一变体不再重试。
⑦ 达到阈值后不得提前收手，其余适用变体继续跑完，失败明细留在 failed_variants，成功与失败各自按变体号升序。
⑧ 预算按靶标指纹各自记账，每个指纹的初始额度都是 total_budget，按执行次数硬顶，重试不得穿透预算；耗尽即截断，未执行变体进 skipped_variants，coverage_complete 置假。
⑨ 只有适用变体全部执行完才把 coverage_complete 置真；被筛掉的变体不算未覆盖。
⑩ 全部变体都被筛掉时报 unverifiable，不能报不可利用。
⑪ runner 返回无法识别的值报 MATRIX_INVALID_RESULT 且本次结果不落库；reset_target 清空该靶标的结果并返回清掉的条数，预算恢复满；result_for 对没有跑过的变体返回 None。

硬约束：预算与执行次数是硬边界，任何重试路径都不得超额；同一指纹的阈值计数只认去重后的变体，重复执行不得把成功数刷高，reset 之后重新起算；快照恢复后变体表、各靶标的逐变体结果与预算余量都要在。stats 返回 variants、runs、executions 三个累计计数，executions 记真实执行次数。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，不改测试、不跳过、不按固定输入写死；预算截断与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: exploit_payload_matrix
- typeName: ExploitPayloadMatrix
