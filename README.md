# batch49-py-safety-b-29

## 题面

误报抑制台账这季度被审计问住：一条笼统的路径抑制在三个月里压掉了大半个仓库的告警，配置里却只写着一行 pattern；有人用重复上报把某条抑制的覆盖率刷到 90%，实际只覆盖三个文件；过期两年的抑制和从未命中的抑制混在一起，没人敢删；值班组每季度手工拼一张表，规则一多就漏，清理建议也没人签字确认；审计跑到一半有人改了配置，报告里前后两段的规则数对不上。这次审计要求给出可复核的时点快照，谁在什么时候看到哪一版配置都要能追。范围是 gsb/batch49-py-safety-b-29 的 suppression_blast_radius_auditor.py，类型 SuppressionBlastRadiusAuditor，负责只读审计抑制规则的影响面。

公开接口：SuppressionBlastRadiusAuditor(*, overbroad_ratio=0.6, min_hits=3, window_days=30)；load_rules(rules)；rules()；record_hits(hits)；coverage(*, now)；audit(*, now)；rule_detail(rule_id, *, now)；cleanup(*, now)；audit_history()；snapshot()；restore(state)；stats()。规则字段 rule_id、pattern、created_at、expires_at、enabled；命中字段 hit_id、rule_id、path、line、batch_id、observed_at；时间均为带时区的 RFC3339 字符串。拒绝操作抛 AuditError(code, detail)。

故障与边界：①加载规则整表替换，rule_id 重复报 AUDIT_RULE_DUPLICATE，字段缺失或时间非法报 AUDIT_RULE_INVALID，失败时配置版本不增；②命中必须指向已加载规则，未知规则报 AUDIT_UNKNOWN_RULE，hit_id 重复报 AUDIT_HIT_DUPLICATE，整批原子拒绝；③覆盖率按去重后的命中计算，同一路径重复上报不得抬高占比；④统计窗口为 now 往前 window_days 天，窗口外的命中不参与分母与分子；⑤分母是启用规则在窗口内覆盖的全部去重路径数，禁用规则不参与；⑥单条规则去重命中数达到 min_hits 且路径占比达到 overbroad_ratio 才标过宽；⑦从未命中的抑制不能判为过宽，只能进入待清理清单；⑧过期规则与从未命中的启用规则分别列出并合并去重，按规则标识排序；⑨审计只读，不写回规则文件，报告带 snapshot_at 与配置版本，审计期间配置被改时标注快照时点，不能混用两个版本的规则；⑩stats 返回 rules、enabled、hits、batches、config_version、audits；⑪快照深拷贝规则、命中、版本与审计历史，顶层键为 rules、hits、config_version、history、audits，恢复非法结构报 AUDIT_SNAPSHOT_INVALID 且原状态不变。

硬约束：命中写入与审计读取全程持锁，并发写入不得丢条或重复；快照恢复后配置版本、命中与历史必须一致；审计结果只依赖规则、命中与 now 三个输入，不得读取系统时钟或环境变量；待清理清单与过宽清单不得互相包含；审计重复执行对同一配置与同一 now 输出完全一致。

验收：python3 -m pytest -q tests 共 15 条必须全过；不得修改测试、跳过或按固定输入写死；报告与清单顺序固定，便于逐条复核；快照含审计次数；并发与重启自行验证；只做本地实现，不修改任何抑制配置。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: suppression_blast_radius_auditor
- typeName: SuppressionBlastRadiusAuditor
