# batch49-py-safety-b-26

## 题面

告警平台这周连续被投诉：同一处 XSS 被两个扫描器各报一次，值班同学手工合并时把位置相邻但漏洞标识不同的两条并成一条，事后追溯发现原始条目丢了；另有一批条目同时对上三条候选，谁也说服不了谁，最后随机留了一条；上周人工判过“不同”的一对，本周重扫置信度更高，系统又自动合了。三个人各自按自己的口径处理，交接时只留下一句“已合并”，既查不到原始条目，也说不清是谁下的判断。范围是 gsb/batch49-py-safety-b-26 的 cross_tool_dedup_aligner.py，类型 CrossToolDedupAligner，负责跨扫描器告警对齐、合并与人工裁决留痕。

公开接口：CrossToolDedupAligner(*, merge_threshold=80, proximity=5, max_alerts=10000)；alert_key(source, vuln_id, path, line)；ingest(scan_id, alerts)；alerts_for(scan_id)；compare(left_key, right_key)；align(scan_id)；pending(scan_id)；adjudicate(left_key, right_key, verdict, *, operator, note="")；decisions()；decision_for(left_key, right_key)；snapshot()；restore(state)；stats()。告警键固定为 source|vuln_id|path|line，拒绝操作抛 AlignError(code, detail)。

故障与边界：①告警缺 source、vuln_id、path，或 line 不是非负整数时整批拒绝，detail 记下标，已有扫描与计数不变；②告警键按固定格式生成，ingest 返回字典序排序的键；③置信度为百分制：漏洞标识相同 50，弱点类型相同 20，路径相同 15，行号差 0 加 15、1 到 proximity 加 10、proximity+1 到四倍 proximity 加 4，其余 0，满分 100；④reasons 只列命中项，顺序为 vuln_id、weakness、path、line_exact 或 line_near；⑤同来源不可比，漏洞标识不同的两条不得自动合并，即使位置相邻；⑥默认阈值 80，达到阈值成组，低于阈值保留两条待裁决并记 below_threshold；⑦一个来源条目被多条匹配覆盖时只取置信度最高的一条，其余降级待裁决并记 duplicate_coverage；⑧候选按置信度降序、左右键升序处理，同一扫描重复对齐不得改变结果或重复计数；⑨组标识由组内排序后的键拼接，sources 升序，输出按组标识排序，待裁决按置信度降序；⑩裁决只接受 same 与 different，非法 verdict 先报 DECISION_INVALID，operator 去空白后为空报 OPERATOR_REQUIRED，再校验两侧告警存在，缺失报 ALERT_UNKNOWN；⑪人工判 same 的低于阈值对，重扫必须合并并标记 adjudicated，判 different 的对后续重扫即使置信度升高也不得自动合并，裁决按无序对持久化，后一次覆盖前一次并递增序号；⑫快照深拷贝，顶层键为 alerts、results、decisions、seq，分别存告警、对齐结果、裁决与序号，恢复时校验结构，非法报 SNAPSHOT_INVALID 且原状态不变。

硬约束：ingest 与 adjudicate 全程持锁，快照与恢复不得暴露半更新状态；重复对齐幂等，stats 的 groups、pending 不得翻倍；恢复后重扫结果与恢复前逐字段一致；同一输入两次对齐的组标识、置信度与待裁决定序必须完全一致；恢复后不得残留旧对象的可变引用；单次扫描告警数不得超过 max_alerts，超出报 ALERT_CAPACITY。

验收：python3 -m pytest -q tests 15 条必须全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现，不接外部扫描器。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: cross_tool_dedup_aligner
- typeName: CrossToolDedupAligner
