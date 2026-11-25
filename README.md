# batch49-py-safety-b-21

## 题面

周一凌晨的告警复盘会上，安全运营发现一批超危漏洞在资产冻结期内被重复计成逾期：运维冻结了数据库主机，修复时限却没有顺延；豁免单到期后系统把剩余时间重新按满额算，导致本该继续计时的漏洞被误判为还有一周；一个已关闭的漏洞因误报被重新打开后又拿到了一整段新时限。范围是 gsb/batch49-py-safety-b-21 的 vuln_sla_escalator.py，类 VulnSlaEscalator，异常 SlaError(code, detail)，记录类型 VulnRecord、EscalationEvent、FreezeWindow、Exemption。

公开接口：VulnSlaEscalator(*, sla_seconds=None, escalate_step=86400, max_level=3, max_vulnerabilities=200, max_freeze_windows_per_asset=64, clock=None)；open_vulnerability(vuln_id, asset_id, severity, confirmed_at, now=None)；add_freeze_window(asset_id, start, end)；grant_exemption(vuln_id, until, now=None)；revoke_exemption(vuln_id, now=None)；close_vulnerability(vuln_id, resolution, now=None)；reopen_vulnerability(vuln_id, now=None)；evaluate(now=None)；get_vulnerability(vuln_id)；escalation_history(vuln_id=None)；snapshot()；restore(state)；stats()。

故障与边界：

①严重度默认时限为 critical 86400 秒、high 259200 秒、medium 604800 秒、low 1209600 秒，sla_seconds 可按严重度覆盖；到期时刻从 confirmed_at 起算，记录状态初始为 open，升级级别为 0；VulnRecord 含 vuln_id、asset_id、severity、status、confirmed_at、due_at、escalation_level、exemption_until、freeze_seconds。

②open_vulnerability 对标识、资产、严重度完全相同的重复登记按幂等处理，返回原记录且不重复计数；同标识但资产或严重度不同、对已关闭标识再次登记都抛 SLA_INVALID_TRANSITION。

③空标识、空资产、未知严重度、非法时间戳抛 SLA_INVALID_REQUEST，detail 指出字段名；漏洞数超过 max_vulnerabilities 抛 SLA_CAPACITY_EXCEEDED。

④冻结窗口按资产登记，start 必须早于 end；窗口与计时区间重叠的秒数从到期时刻顺延，多个窗口按并集逐段累加，同一资产窗口数超过 max_freeze_windows_per_asset 抛 SLA_CAPACITY_EXCEEDED，返回该资产当前窗口总数。

⑤豁免从授予时刻暂停计时，到期后从到期点接着计算剩余时限，不得重置为满额；撤销豁免后从撤销时刻恢复剩余时限；重复授予或对已关闭漏洞授予抛 SLA_INVALID_TRANSITION，until 不晚于当前时刻抛 SLA_INVALID_REQUEST；Exemption 含 vuln_id、granted_at、until、revoked_at。

⑥已关闭漏洞再次打开沿用原确认时刻与剩余时限，不能重新起算；关闭时 resolution 为空抛 SLA_INVALID_REQUEST，对已关闭漏洞再次关闭、对未关闭漏洞重开都抛 SLA_INVALID_TRANSITION；close 与 reopen 返回更新后的 VulnRecord。

⑦evaluate 只处理状态为 open 且已逾期的漏洞，按 due_at、due_at+escalate_step、due_at+2×escalate_step 逐级记录，最高 max_level 级；单次调用补齐所有已达到的级别，同一时刻重复调用不得重复记录，已关闭漏洞不得升级；升级级别一旦记录只增不减，后续冻结或豁免不得回退已产生的级别。

⑧升级事件按到达阈值的时间记录，事件时间取阈值本身而非调用时间；evaluation 返回本次新增事件，escalation_history 按时间、漏洞标识、级别升序返回全部事件。

⑨快照键固定为 version、vulnerabilities、freeze_windows、exemptions、events、counters，vulnerabilities 与 freeze_windows 保存标识到记录的映射、events 为列表、counters 为计数映射；快照必须深拷贝，键缺失、版本不符或映射与列表类型错误都抛 SLA_SNAPSHOT_INVALID 且当前状态不变。

⑩stats 固定返回 opened、closed、reopened、escalations、freeze_windows、exemptions 六个计数，每次成功操作只加一，快照恢复后计数完整保留；未知漏洞标识统一抛 SLA_UNKNOWN_VULNERABILITY。

⑪时间全部为 UTC ISO-8601 字符串，输出统一到 +00:00 秒级；除构造注入的 clock 外不得读取系统时间，结果必须可复算。

⑫同一漏洞的登记、升级与冻结重算必须持锁原子完成；并发登记不得产生重复记录，并发 evaluate 不得丢失或重复升级事件。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与快照恢复自行验证；只做本地实现，不依赖第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: vuln_sla_escalator
- typeName: VulnSlaEscalator
