# batch49-py-fix-a-05

## 题面

安全周会上，扫描平台一口气报了四百多个依赖漏洞，研发反问哪几个是真的能被外部请求调到；安全团队拿不出调用链，只能按 CVSS 排了个序，结果真正被利用的那个库排在第七页。问题落在 gsb/batch49-py-fix-a-05 的 reachability_fix_planner.py，类 ReachabilityFixPlanner，用它把函数级调用图、入口可达性与修复顺序算成一份带证据的清单。调用图与漏洞登记都在内存里维护，不解析真实二进制。

公开入口：ReachabilityFixPlanner(*, max_nodes=256, max_edges=1024)；add_node(name, *, package, version="0.0.0")；add_edge(caller, callee)；mark_entry(node, *, route="")；add_vulnerability(vuln_id, *, symbol, severity, fixed_in=None)；analyze()；plan(*, limit=None)；evidence_for(vuln_id)；remove_edge(caller, callee)；remove_vulnerability(vuln_id)；snapshot()；restore_state(state)；stats()。拒绝与失败统一抛 ReachabilityError(code, detail)。

要落地的行为：①节点名、package 与 version 都必须非空，重名报 REACH_DUPLICATE_NODE；节点总数受 max_nodes 硬顶，超限 REACH_CAPACITY_EXCEEDED。②边只接受已登记的两个节点，缺失任一端报 REACH_NODE_UNKNOWN；自环报 REACH_INVALID_EDGE；重复登记同一条边是幂等的，不报错、不增加边计数、不占边上限；边总数受 max_edges 硬顶。③mark_entry 要求节点已登记，否则 REACH_NODE_UNKNOWN；同一节点重复标记只更新路由字符串，入口计数不变。④add_vulnerability 的 vuln_id 非空且不得重复，severity 只允许 critical、high、medium、low，symbol 必须是已登记节点，fixed_in 可为空；分别报 REACH_DUPLICATE_VULN、REACH_INVALID_SEVERITY、REACH_NODE_UNKNOWN、REACH_INVALID_REQUEST。⑤analyze 从所有入口做广度优先，取到脆弱符号的最短调用链；证据链从入口开始、到脆弱符号结束，中间每一对相邻节点都必须是已登记的边；入口本身就是脆弱符号时，证据链只含该入口、深度为 0。⑥证据链必须是简单路径，图里有环时按最短路径截断，重复节点不得写进证据，遍历不得因环不收敛。⑦等长路径按整条节点串（含入口名）做字典序比较取最小；两个入口等长时同样按整串比较，而不是按登记先后。⑧可达漏洞的 depth 是证据链边数，优先级为 severity 基准分（critical 400、high 300、medium 200、low 100）加 100 减 10 乘 depth 的加成，加成下限为 0；不可达漏洞 depth 为 -1、证据为空元组、entry 为 None，只取基准分。⑨analyze 结果按优先级降序、vuln_id 升序排列；plan 复用同一顺序返回 vuln_id，limit 为空取全部，limit 必须是正整数否则 REACH_INVALID_REQUEST。⑩evidence_for 独立计算并返回与 analyze 一致的证据链，未知漏洞报 REACH_VULN_UNKNOWN，不可达漏洞返回空元组；删除边返回是否真的删掉，删除不存在的漏洞返回 False。⑪任何增删边、增删漏洞之后再次 analyze 都必须反映新图，不得返回上一次的结论；stats 固定返回 nodes、edges、entries、vulns、analyses、reachable、unreachable 七个键，后两项是最近一次 analyze 的可达与不可达计数。⑫快照深拷贝节点、边、入口路由、漏洞登记与计数，恢复后图与统计完整复原；非法结构、边引用未知节点或严重度非法一律 REACH_SNAPSHOT_INVALID 且原状态不变。

硬约束：add_node、add_edge、mark_entry、add_vulnerability、analyze、remove_edge、remove_vulnerability 共用一把可重入锁，analyze 只能在一次完整写入之后读取邻接表，并发增删不得让某条边只出现在一侧；快照恢复后节点包归属、边、入口路由、漏洞元数据与计数必须完整复原，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: reachability_fix_planner
- typeName: ReachabilityFixPlanner
