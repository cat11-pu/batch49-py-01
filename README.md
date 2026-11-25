# batch49-py-safety-b-22

## 题面

合规审计在季度抽查里发现，漏洞豁免单的流转记录对不上：有的单子没批就直接生效，有的过期后仍能挡掉扫描告警，续期之后旧版本批准人凭空消失；更严重的是，一条只批准给支付域标签的豁免，被同规则下的测试资产借用了。范围是 gsb/batch49-py-safety-b-22 的 vuln_exemption_workflow.py，类 VulnExemptionWorkflow，异常 ExemptionError(code, detail)，记录类型 ExemptionRecord、AssetRecord。

公开接口：VulnExemptionWorkflow(*, default_ttl=604800, max_live_exemptions=100, clock=None)；register_asset(asset_id, tags, now=None)；update_asset_tags(asset_id, tags, now=None)；request_exemption(exemption_id, rule_id, asset_tags, requested_by, ttl_seconds=None, now=None)；approve_exemption(exemption_id, approver, now=None)；reject_exemption(exemption_id, approver, reason, now=None)；activate_exemption(exemption_id, now=None)；renew_exemption(exemption_id, ttl_seconds, approver, now=None)；revoke_exemption(exemption_id, reason, now=None)；expire_due(now=None)；is_exempted(rule_id, asset_id, now=None)；get_exemption(exemption_id)；versions(exemption_id)；history(exemption_id)；list_by_state(state)；snapshot()；restore(state)；stats()。

故障与边界：

①状态只允许 requested→approved/rejected/revoked、approved→active/revoked、active→expired/revoked/superseded；拒绝、撤销、过期与 superseded 为终态；非法迁移抛 EXEMPTION_INVALID_TRANSITION，detail 至少含 current 与 allowed 两个键，allowed 是该状态全部合法目标。

②申请时豁免标识、规则标识、申请人不得为空，资产标签不得为空且去重后按字典序保存；重复豁免标识抛 EXEMPTION_DUPLICATE_ID，ttl_seconds 非正数或字段非法抛 EXEMPTION_INVALID_REQUEST。

③ExemptionRecord 含 exemption_id、version、rule_id、asset_tags、state、requested_by、approved_by、created_at、activated_at、expires_at、reason、superseded_by，AssetRecord 含 asset_id、tags、updated_at；申请版本为 1，批准只写 approved_by，拒绝只写 reason 与终态，不写 approved_by。

④激活只接受 approved，expires_at 等于激活时刻加 ttl，ttl 缺省取 default_ttl，激活前 expires_at 必须为空；所有成功迁移都写入 history，request 的 actor 为 requested_by，approve、reject、revoke 的 actor 为操作人，activate 与 expire_due 的 actor 记为 system。

⑤豁免只对声明的规则且资产当前标签包含全部声明标签时生效；未登记资产、规则不符或标签缺项一律返回 False，不得借用给范围外资产；适用性只按最新版本计算，旧版本不参与；register_asset 与 update_asset_tags 都按去重字典序保存标签。

⑥到期按 expires_at 实时判定失效；expire_due 只把已到期且最新版本仍为 active 的豁免落为 expired 并返回迁移条数，重复调用返回 0。

⑦续期只允许 active 且尚未到期，生成版本号加一的新 active 记录，旧版本转为 superseded 并写入 superseded_by，旧版本的批准人、期限与历史不得被覆盖；已到期豁免续期失败后必须停留在 expired，不能回到 active。

⑧资产标签变更后所有豁免的适用性立即按新标签重算，不需要重新申请；被撤销的豁免再次申请必须从 requested 重新走完整审批，不得复活旧批准。

⑨活跃豁免指最新版本处于 requested、approved、active 的总数，受 max_live_exemptions 限制，超限抛 EXEMPTION_CAPACITY_EXCEEDED；终态不计入。

⑩history 按发生顺序返回，每条含 exemption_id、version、from、to、at、actor，续期同时记录旧版本 active→superseded 与新版本新建→active；versions 按版本号升序；list_by_state 只返回最新版本处于该状态且按标识升序，get_exemption 对未知标识抛 EXEMPTION_UNKNOWN。

⑪快照键固定为 version、exemptions、assets、history、counters，exemptions 与 history 是豁免标识到版本列表或事件列表的映射，assets 是资产标识到记录的映射，counters 必须含七个计数键；快照必须深拷贝，键缺失、版本不符或类型错误都抛 EXEMPTION_SNAPSHOT_INVALID 且当前状态不变。

⑫stats 固定返回 requested、approved、rejected、activated、renewed、revoked、expired 七项；时间统一为 UTC ISO-8601 秒级，除注入 clock 外不读系统时间；审批、激活、续期与到期迁移必须持锁原子完成，并发申请不同标识不得丢记录或串版本。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与快照恢复自行验证；只做本地实现，不依赖第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: vuln_exemption_workflow
- typeName: VulnExemptionWorkflow
