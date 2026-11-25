# batch49-py-safety-c-11

## 题面

上周两起值班事故都出在因子找回上：一名用户丢了手机，工单刚开就被客服直接重置了全部因子，事后查不到谁批的；另一名用户在同一账号上同时挂了两张找回单，两张单互相把对方刚补的因子又撤掉，账号整晚登不进去。更麻烦的是客服系统只看得到最终状态，等待期、复核票和冻结时间都没有落库，夜里根本没法向用户解释为什么还不能恢复。安全组复盘后要求把找回流程收进一条状态机里，范围是 gsb/batch49-py-safety-c-11 的 mfa_recovery_caseflow.py，类 MfaRecoveryCaseflow，负责因子丢失工单的等待期、复核、恢复期冻结与快照恢复。

公开接口：MfaRecoveryCaseflow(*, wait_low=0, wait_high=3600, grace=1800, max_new_factors=5)；register_account(*, account, factors=(), now=0)；open_case(*, account, risk, now)；assign_reviewers(*, case_id, reviewers, now)；review(*, case_id, reviewer, approve=True, now)；freeze_factors(*, account, frozen, now)；add_factor(*, account, factor, now)；password_login(*, account)；record_self_recovery(*, account, factors, now)；complete(*, case_id, new_factors, now)；expire(now)；get_case；stats、snapshot、restore。拒绝抛 MfaRecoveryError(code, detail)，时间一律用整数秒。

故障与边界：①低风险等待 0 秒、一次复核，高风险等待 3600 秒、两次复核，未知风险、空账号与空复核人名单都按 MFA_INVALID_REQUEST 拒绝，未登记账号报 MFA_ACCOUNT_NOT_FOUND，工单号按 mfa-0001 递增，工单状态取 waiting、completed、rejected、expired 之一；②每账号同时只允许一张未结工单，重复受理报 MFA_CASE_DUPLICATE，只有完成、否决或失效之后才允许重新开单，复核人名单去重后按字典序保存；③开单即冻结该账号的因子增删，冻结期间 add_factor 报 MFA_FACTORS_FROZEN，密码登录不受影响，冻结账号数要从统计里看到，add_factor 返回排序后的因子元组，freeze_factors 返回切换前的冻结状态；④等待期内复核报 MFA_CASE_WAIT_ACTIVE，未指派的复核人报 MFA_REVIEWER_NOT_ASSIGNED，同一人重复投票报 MFA_REVIEW_DUPLICATE，工单不存在报 MFA_CASE_NOT_FOUND，且重复票不改动已收票数；⑤等待期满且通过票数达到要求才可完成，否则 MFA_REVIEW_INCOMPLETE；⑥完成时撤销旧因子、写入新因子并返回审计序号区间，审计序号从 1 开始且必须连续，撤销与新增的每个因子各占一个审计序号，报告里的旧因子与新因子都按字典序，旧因子与新因子都不许残留；⑦任一否决票立即否决工单并解除冻结，之后 complete 报 MFA_CASE_CLOSED；⑧工单在等待期结束再加 1800 秒后失效，expire 只处理到期工单并返回按字典序排好的工单号，边界时刻按到期处理；⑨等待期内用户自行补回部分因子必须重新计算复核要求并清空已收票，不能借补回直接通过；⑩复核人名单里出现申请人本人时该票无效，等待期顺延，不能只做提示；⑪工单失效关闭后，恢复期冻结的因子增删必须自动解除，不能留下永久冻结；⑫快照深拷贝，顶层字段固定为 version、cases、accounts、stats，恢复时结构不符抛 MFA_SNAPSHOT_INVALID 且原状态不动。

硬约束：同一账号的开单、复核、完成、失效必须串行，并发下不得出现两张未结工单，快照恢复后账本、审计序号、冻结状态和全部计数一致；每账号单张未结工单与每单复核人数上限不得绕过，新因子数量超过 max_new_factors 按 MFA_INVALID_REQUEST 拒绝。stats 返回 opened、completed、expired、rejected、reviews、revoked_factors、frozen，其中 reviews 统计通过与否决的有效票，frozen 为当前冻结账号数。

验收：`python3 -m pytest -q tests` 共 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: mfa_recovery_caseflow
- typeName: MfaRecoveryCaseflow
