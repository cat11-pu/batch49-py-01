# batch49-py-fix-b-08

## 题面

季度变更评审上，安全组把两台防火墙的规则集导出来做交叉审计，结果比预期难看得多：一条很早以前放通整个办公网到 443 的规则，把三年前加的一条只允许财务网段访问 8443 的例外彻底盖住了，后者从来没有生效过；另一组规则在端口上部分重叠，一个放通 8000 到 9000，一个拒绝 8500 到 9500，评审时没人能说清 8600 到底走哪条。更麻烦的是，这些判断平时只靠人肉翻表，变更窗口里两张表各改各的，改完也没有一份能复查的冲突报告。现在要求把判定做成可查询的本地状态：规则按优先级和登记顺序生效，给定五元组要能明确落到哪一条，任意两条规则之间的关系要能批量列出来。入口在 gsb/batch49-py-fix-b-08 的 fw_policy_conflict.py，类 FirewallPolicyAnalyzer，负责规则台账、首匹配查询与冲突归类。

公开入口：FirewallPolicyAnalyzer(*, default_action="deny", max_rules=256)；add_rule(name, *, action, src, dst, proto="tcp", ports=None, priority=100, enabled=True, zone=None)；remove_rule(name)；enable_rule(name, *, enabled=True)；query(src_ip, dst_ip, *, proto="tcp", port=None, zone=None)；explain(src_ip, dst_ip, *, proto="tcp", port=None, zone=None)；conflicts()；summary()；snapshot()；restore(state)；stats()。拒绝与失败统一抛 FwPolicyError(code, detail)。

要落地的行为：①规则名去空白后非空且唯一，重复给 FW_RULE_EXISTS；action 只接受 allow 与 deny；proto 只接受 tcp、udp、icmp、any；src 与 dst 接受 any 或规范 IPv4 CIDR，地址不合法给 FW_ADDRESS_INVALID；规则总数达到 max_rules 后新增给 FW_CAPACITY。②ports 只对 tcp、udp、any 有意义，写成逗号分隔的端口或闭区间，解析后按升序合并，端口范围必须在 1 到 65535 之间，反向区间、越界、空段都给 FW_PORT_INVALID；icmp 规则带 ports 同样给 FW_PORT_INVALID。③生效顺序先按 priority 升序，再按登记序号升序；enabled 为 False 的规则不参与查询与冲突分析，但仍保留在规则表中，重新启用后按原顺序恢复。④query 按生效顺序首匹配，命中返回 action、rule 与 default 为 False；没有命中返回 default_action、rule 为 None 与 default 为 True。⑤query 的 src_ip 与 dst_ip 必须是合法 IPv4 地址；tcp 与 udp 必须给出 1 到 65535 的 port；icmp 不允许带 port；这些不满足给 FW_QUERY_INVALID。⑥端口匹配按闭区间判断，规则 ports 为空表示任意端口；目标地址匹配按 CIDR 包含关系，any 匹配所有地址。⑦explain 返回与 query 相同的结论，并带上 evaluated 列表，按顺序列出真正检查过的启用规则名，直到命中的那一条为止。⑧冲突分析只在启用规则之间两两进行：前一条规则的源、目的、协议、端口完全覆盖后一条且动作不同，归为 shadow；完全覆盖且动作相同归为 redundant；只在部分匹配空间重叠归为 correlated。⑨冲突条目必须带 kind、earlier、later 三个字段，earlier 是生效顺序更靠前的那条，结果按 earlier 与 later 的生效顺序稳定排序，同一条规则对只出现一次。⑩不同 zone 的规则属于不同策略命名空间：zone 为 None 的是全局规则，查询带 zone 时只命中全局规则与同 zone 规则；两条 zone 不同且都非 None 的规则即使匹配空间相同也不算冲突。⑪summary 至少给出 rules、enabled、conflicts、default_action；stats 至少给出 added、removed、queries；remove_rule 成功返回 True，规则不存在返回 False。⑫snapshot 深拷贝规则表、序号与计数；restore 结构不符给 FW_SNAPSHOT_INVALID 且原状态可用；从快照恢复后，快照之后新增或删除的规则都不得残留。

硬约束：新增、删除、启用开关、查询与冲突分析必须在同一把可重入锁内完成，query 与 explain 只能看到完整的规则快照，不得读到只加了一半的规则；多个线程并发查询同一条流必须得到同一个规则名。快照与恢复整体替换规则表与计数，恢复失败不得留下半套状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: fw_policy_conflict
- typeName: FirewallPolicyAnalyzer
