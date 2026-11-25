# batch49-py-fix-b-01

## 题面

一家做边缘加速的服务商把会话恢复率写进了季度目标，安全复核时审计员却在票据记录里翻出三处说不通的地方：同一张票据在两个机房各被接受了一次；一张早就过了生命期的票据仍然放行，理由是签发端刚轮换过密钥；还有一张票据的绑定位被换成另一台客户端的指纹，居然也验过了。更麻烦的是，轮换脚本跑完之后，旧票据的受理窗口到底还剩多久，没人能给出确定答案。入口在 gsb/batch49-py-fix-b-01 的 ticket_replay_guard.py，类 TicketReplayGuard，负责票据签发、密钥代数管理、验签与一次性兑换账本。

公开入口：TicketReplayGuard(*, ticket_lifetime=300, clock_skew=30, max_keys=4, key_grace=120, replay_window=60, max_ledger=256)；add_key(secret)；retire_key(epoch, *, now=0)；keys()；active_epoch()；mint(subject, *, now=0, client_binding=None, ttl=None, reusable=False)；redeem(token, *, now=0, client_binding=None)；prune(*, now=0)；ledger_size()；snapshot()；restore(state)；stats()。拒绝与失败统一抛 TicketReplayError(code, detail)。

要落地的行为：①密钥环按代数管理。secret 至少 16 个字符，否则 TICKET_KEY_INVALID；每次 add_key 分配比现有最大代数大一的新代数并返回；未退役密钥数达到 max_keys 时拒绝并给 TICKET_KEYRING_FULL；已退役密钥不占名额。②退役只对非活动代数生效：未知代数给 TICKET_KEY_UNKNOWN，退役活动代数给 TICKET_KEY_ACTIVE，重复退役返回 False；退役不改变活动代数，只改变该代数的验签窗口，keys 按代数升序给出 epoch、retired_at 与 active。③签发时 subject 去空白后不得为空，给了 client_binding 就不能是空串，否则 TICKET_INVALID；ttl 不传取 ticket_lifetime，显式值必须是 1 到 ticket_lifetime 的整数，否则 TICKET_TTL_INVALID；expires_at 等于 now 加 ttl；票据号从 tk-1 起单调递增；始终用活动代数签发；mint 返回 ticket_id、subject、epoch、issued_at、expires_at、reusable 与 token。④token 是四段点分的 v1.代数.载荷.mac，mac 由该代密钥对版本、代数与载荷做 HMAC-SHA256 并截断为 32 位小写十六进制，载荷至少承载票据号、主体、绑定位、签发时间、到期时间与可重用标记。⑤验签顺序固定：结构、代数存在、签名、载荷、时钟、到期、绑定、重放。结构不合法给 TICKET_TOKEN_INVALID，代数不存在给 TICKET_KEY_UNKNOWN，签名不符给 TICKET_MAC_INVALID。⑥代数退役后 key_grace 秒内仍可验签，超过宽限后该代数签发的票据一律 TICKET_KEY_EXPIRED，即使票据自身的到期时间还没到。⑦签发时间晚于 now 超过 clock_skew 的票据必须给 TICKET_NOT_YET_VALID，不得因为客户端时钟偏差放宽到期判定。⑧redeem 传入的 binding 必须与签发时逐字相同，都为空也算相同，不同给 TICKET_BINDING_MISMATCH；绑定失败的尝试不得写入账本，也不得消耗票据。⑨一次性票据首次兑换成功后写入账本，条目在票据到期前一直有效，重复兑换给 TICKET_REPLAYED；可重用票据不记一次性，early_data 只在 now 减 issued_at 不超过 replay_window 时为真，兑换成功返回 ticket_id、subject、epoch、issued_at、expires_at、reusable 与 early_data。⑩写入账本前先清掉已到期条目，清理后仍达到 max_ledger 时必须拒绝本次兑换并给 TICKET_LEDGER_FULL，不得为了腾位置淘汰未到期条目。⑪prune 只删除已到期的账本条目并返回条数，重复调用返回 0；退役密钥超过宽限的由 prune 一并丢弃但不计入返回数；snapshot 深拷贝密钥环、账本、票据序号与计数，返回 keys、ledger、seq、stats 四个键；restore 结构不符给 TICKET_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 minted、redeemed、replayed、rejected、pruned、retired，rejected 累计所有被拒的兑换尝试。

硬约束：验签、绑定比对、账本查重与写入必须在同一把可重入锁内完成，同一张一次性票据被多线程并发兑换时只能有一个线程成功，其余必须拿到 TICKET_REPLAYED，失败尝试不得留下账本条目。快照与恢复整体替换密钥环、账本与计数，恢复失败不得留下半套状态；从快照恢复后，快照时刻尚未兑换的票据可以重新兑换，已经兑换过的票据不得再次放行。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: ticket_replay_guard
- typeName: TicketReplayGuard
