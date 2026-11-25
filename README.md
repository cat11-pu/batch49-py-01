# batch49-py-fix-b-09

## 题面

安全运营中心的告警队列最近被同一类事件刷了屏：一条针对管理后台路径的规则在十分钟里报了两千多条，值班同学按源地址去重之后发现只有十一个真实来源；另一次演练里，一条依赖两个特征词同时出现的规则只匹配到前半段就开始报警，复盘时才发现扫描窗口被截断了。更麻烦的是，规则热加载期间有几秒钟告警数量掉到零，事后无法判断是流量安静还是规则表只换了一半。组里的要求是让匹配引擎自己承担这些账：规则怎么装载、报文扫哪一段、重复告警怎么合并、限速与抑制什么时候生效，都要能复算。入口在 gsb/batch49-py-fix-b-09 的 ids_signature_engine.py，类 IdsSignatureEngine，负责规则装载、报文匹配、告警合并与抑制账本。

公开入口：IdsSignatureEngine(*, max_rules=128, max_alerts=256, suppress_window=30, max_payload_bytes=8192)；load_rules(rules)；add_rule(rule)；remove_rule(sid)；rule(sid)；inspect(packet, *, now=0)；suppress(sid, *, scope="global", ttl=60, now=0)；unsuppress(sid, *, scope=None)；alerts()；prune(*, now=0)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 IdsSignatureError(code, detail)。

要落地的行为：①规则必须有正整数 sid、非空 msg 和至少一个非空 content 或 contents，重复 sid 给 IDS_RULE_EXISTS，规则总数超过 max_rules 给 IDS_CAPACITY，字段缺失或类型不对给 IDS_RULE_INVALID；nocase、offset、depth、priority 缺省分别为 False、0、不限、1。②load_rules 接收规则列表并整体替换当前规则表，批次里任何一条不合法都不得安装任何规则，原来的规则表保持可用；成功返回安装条数。③inspect 的报文必须含 src_ip、dst_ip、src_port、dst_port、proto、payload、direction 七个字段，direction 只接受 to_server 与 from_server，缺字段或取值非法给 IDS_PACKET_INVALID。④content 与 contents 里的每个特征都要在扫描窗口内出现，全部命中才算该规则命中；nocase 为真时双方统一转小写比较。⑤offset 与 depth 共同界定扫描窗口，窗口从 offset 开始、长度为 depth，depth 缺省时扫到载荷末尾；窗口之外的内容不得命中。⑥单条规则命中只产生一个告警，即使同一特征在报文里出现多次也不得重复计数；命中结果按 priority 升序、再按 sid 升序返回。⑦flow 为 to_server 或 from_server 时只匹配同方向报文，为 any 时两侧都匹配；proto 同理；dst_port 非空时必须与报文目的端口相等。⑧alerts 以 sid 加源地址加目的地址为键在 suppress_window 秒内合并，重复命中只把既有告警的 count 加一并刷新时间，不新增条目，合并次数记入 coalesced。⑨规则带 threshold 时按 count 与 seconds 限速，窗口内已经发出的告警数达到 count 后，本轮其余命中返回空列表且 rate_limited 加一，窗口滚过之后恢复。⑩suppress 按 sid 与 scope 登记抑制，ttl 必须为正整数，expires_at 等于 now 加 ttl；作用域为 global 时该规则所有报文都被抑制，作用域为 CIDR 时只抑制源地址落在该网段的报文；重复登记同一 sid 与 scope 按覆盖处理，unsuppress 返回删除条数。⑪告警条目数达到 max_alerts 后新增告警必须挤掉最旧的一条并把 alerts_dropped 加一；prune 删除已过期的抑制项并返回条数；remove_rule 成功后该规则不再参与匹配，重复删除返回 False。⑫snapshot 深拷贝规则表、告警、抑制项、限速窗口与计数；restore 结构不符给 IDS_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 rules_loaded、alerts、coalesced、rate_limited、suppressed、alerts_dropped、removed。

硬约束：规则装载、匹配、告警写入、限速与抑制判定必须在同一把可重入锁内完成，inspect 只能看到换表前或换表后的完整规则集，不得出现半套规则；多个线程同时对同一报文 inspect 时，合并计数不得丢更新。快照与恢复整体替换规则、告警、抑制与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后新增的规则与告警都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: ids_signature_engine
- typeName: IdsSignatureEngine
