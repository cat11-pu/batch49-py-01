# batch49-py-fix-b-07

## 题面

路由监控大屏在前天上午同时亮了三块区域：一块属于某个内容网络，一块属于一家云厂商，还有一块是自建机房的上游。值班同学把三段时间线叠在一起才发现，真正异常的不是流量突增，而是同一个地址段先后出现了两个不同的源 AS，其中一次只持续了十一分钟，另一次挂在一条更具体的 /24 上，看聚合曲线完全看不出来。事后复盘时有人说当时看到过 ROA 校验不一致的告警，但没人能确认那条告警被哪几个对等体同时观测到，也没人记得它后来是怎么消失的。现在的要求是把研判过程固定成本地状态：每次宣告都要和 ROA、历史基线比对，异常结论必须有多个对等体背书，事件要有生命周期。入口在 gsb/batch49-py-fix-b-07 的 bgp_origin_watch.py，类 BgpOriginWatch，负责 ROA 台账、宣告观测、劫持确认与事件账本。

公开入口：BgpOriginWatch(*, confirm_peers=2, max_events=256, max_prefixes=256, roa_grace=0)；add_roa(prefix, origin_as, *, max_length=None, expires_at=None)；withdraw_roa(prefix, origin_as)；observe(prefix, origin_as, path, *, peer, now=0)；assessment(prefix)；events(*, include_resolved=False)；ack(event_id)；prefixes()；prune(*, now=0)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 BgpWatchError(code, detail)。

要落地的行为：①前缀必须是规范写法的 IPv4 CIDR，主机位非零或掩码越界给 BGP_PREFIX_INVALID；origin_as 必须是 1 到 4294967295 的整数，否则 BGP_ASN_INVALID；path 必须是非空的 AS 整数列表，重复 AS 给 BGP_PATH_LOOP，空列表给 BGP_PATH_INVALID；peer 去空白后为空给 BGP_PEER_INVALID。②add_roa 的 max_length 必须在当前前缀长度到 32 之间，否则 BGP_ROA_INVALID；同一前缀与 origin 重复登记按覆盖处理；withdraw_roa 删除成功返回 True，重复删除返回 False。③有未过期 ROA 覆盖时，origin 与 ROA 一致且前缀长度不超过 max_length 判为 valid；没有任何违规时不得产生事件。④宣告的前缀长度超过覆盖 ROA 的 max_length 时判为 invalid_length，即使 origin 相同也算违规，严重度为 medium。⑤没有 ROA 覆盖时，首次出现的 origin 记为基线并返回 first_seen；同 origin 后续返回 unknown；换 origin 返回 origin_change 并进入待确认。⑥有 ROA 覆盖但 origin 不符时，若宣告前缀比 ROA 前缀更具体，kind 为 more_specific、severity 为 critical，否则 kind 为 roa_mismatch、severity 为 high；无效长度的 severity 为 medium。⑦待确认的违规必须由至少 confirm_peers 个不同 peer 观测到才升级为 confirmed_hijack 并生成事件；同一个 peer 重复观测不得增加计数；未达门槛时返回 pending 并带上已观测的 peer 列表。⑧同一前缀与同一违规 origin 只生成一个未关闭事件，后续观测继续返回同一 event_id，不得重复计数；事件号从 bgp-1 单调递增。⑨再次观测到 ROA 规定 origin 或无 ROA 场景下的基线 origin 时，该前缀的未关闭事件标记为 resolved 并带 resolved_at；events 默认不返回已 resolved 与已 ack 的事件，include_resolved=True 时返回全部历史。⑩事件数达到 max_events 后新增事件必须挤掉最旧的一条，并把 events_dropped 加一；ack 对未 ack 的事件返回 True 并使其从默认视图消失，重复 ack 返回 False。⑪ROA 带 expires_at 时，now 达到 expires_at 加 roa_grace 之后该 ROA 视作不存在，相关前缀回落到基线判定，不得继续产生 invalid_length 或 roa_mismatch。⑫snapshot 深拷贝 ROA、基线、待确认集合、事件与计数；restore 结构不符给 BGP_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 observations、valid、offending、events、events_dropped、resolved、acked。

硬约束：观测、基线更新、peer 计数与事件写入必须在同一把可重入锁内完成，多个线程用不同 peer 同时观测同一个违规宣告时只能生成一个事件，stats 的 events 只能加一，返回里的 peers 必须完整覆盖到门槛。快照与恢复整体替换 ROA、基线、待确认集合与事件，恢复失败不得留下半套状态；从快照恢复后，快照之后新增的基线与事件都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: bgp_origin_watch
- typeName: BgpOriginWatch
