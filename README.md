# batch49-py-fix-b-10

## 题面

分支机构做专线割接的那天，总部运维在隧道日志里看到一段解释不通的序列：同一个 IKE 重密钥请求被上游设备重发了四次，本地每次都新建了一代 SA，四代 SA 在半小时内同时存在，最后几条业务流被分到了不同的子 SA 上；更奇怪的是，删除通知到达时既没有校验目标代次，也没有区分当前活动代次和正在收尾的旧代次，一条迟到的删除把刚建好的新 SA 直接抹掉了。割接结束后，双方对“消息号窗口里哪些请求已经处理过”各执一词，翻日志只能看到结果，看不到判定过程。现在要求把控制面的账做在本地：每个发起方的 SA 代次、消息号窗口、重复请求的缓存应答、删除与收尾都要有确定状态。入口在 gsb/batch49-py-fix-b-10 的 ike_rekey_ledger.py，类 IkeRekeyLedger，负责代次账本、消息窗口、重密钥协调与删除处理。

公开入口：IkeRekeyLedger(*, window_size=64, max_sas=32, overlap_seconds=120, max_initiators=16, sa_lifetime=3600)；create_sa(initiator, *, now=0, spi=None)；receive(initiator, message_id, kind, payload=None, *, now=0)；rekey(initiator, *, now=0, nonce=None)；delete_sa(initiator, generation, *, now=0)；generations(initiator)；window(initiator)；state(initiator)；prune(*, now=0)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 IkeLedgerError(code, detail)。

要落地的行为：①create_sa 的 initiator 去空白后非空，否则 IKE_INITIATOR_INVALID；重复初始化给 IKE_INITIATOR_EXISTS，同时存在的发起方达到 max_initiators 后新增给 IKE_INITIATOR_LIMIT；显式 spi 去空白后为空给 IKE_SPI_INVALID，未提供时按 spi-加代次自动生成。②每代 SA 记录 generation、spi、state、created_at、expires_at 与 retire_at，首代 generation 为 1、state 为 active、expires_at 为 now 加 sa_lifetime、retire_at 为空；同一发起方内 spi 不得重复。③receive 的 message_id 必须是 0 以上的整数，否则 IKE_MSG_INVALID；kind 只接受 rekey_request、delete_request、informational，其他给 IKE_KIND_INVALID；未知发起方给 IKE_UNKNOWN_INITIATOR。④消息窗口从最高已收号向前覆盖 window_size 个号：首个消息直接接受；新号比当前最高号大出超过 window_size 给 IKE_MSG_OUT_OF_WINDOW；号落在最高号减 window_size 之下且从未处理过给 IKE_MSG_TOO_OLD；窗口内的乱序号正常接受。⑤同一个 message_id 与同样的 kind 与 payload 重放时直接返回第一次的应答并带 duplicate 为真，不得新建代次或推进窗口；同一个 message_id 换成不同 payload 给 IKE_MSG_CONFLICT，且第一次的缓存应答保持有效。⑥rekey_request 的 payload 必须是带非空 nonce 的对象，否则 IKE_PAYLOAD_INVALID；处理成功后新代次为 active，旧代次转 closing 并把 retire_at 记为本次 now，应答带 action 为 rekeyed 与新一代次号。⑦本地 rekey 与收到 rekey_request 走同一套代次推进逻辑，nonce 只作记录；旧代次在 retire_at 加 overlap_seconds 之前仍可被删除与收尾，但不得再作为新协商的发起代次。⑧delete_request 的 payload 必须是带整数 generation 的对象；目标代次不存在给 IKE_SA_UNKNOWN，目标仍是活动代次给 IKE_SA_ACTIVE，closing 代次可以删除；delete_sa 成功返回 True，对同一代次重复调用返回 False；delete_request 对已删除代次的重复通知按幂等成功应答，不得再改状态。⑨所有 kind 与 payload 的合法性必须在推进消息窗口之前判定，失败请求不得占用消息号、不得留下缓存应答、不得改变代次状态。⑩window 返回 highest、size 与按升序排列的 seen 列表；state 返回 active_generation、active_spi 与最高已收号；generations 按代次升序返回全部代次。⑪prune 在 now 达到 retire_at 加 overlap_seconds 时删除 closing 代次并返回删除条数，未到边界返回 0；代次总数达到 max_sas 后新的重密钥给 IKE_SA_LIMIT。⑫snapshot 深拷贝代次、消息窗口、缓存应答与计数；restore 结构不符给 IKE_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 sas_created、messages、rekeys、deletes、duplicates、rejected、pruned。

硬约束：消息号判定、重复应答缓存、代次推进与删除必须在同一把可重入锁内完成，多个线程同时提交同一个 message_id 与同样的 payload 时只能有一个请求真正建代次，其余必须拿到 duplicate 为真的同一份应答，rekeys 只能加一。快照与恢复整体替换代次、消息窗口、应答缓存与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后新增的代次与消息号都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: ike_rekey_ledger
- typeName: IkeRekeyLedger
