# batch49-py-safety-d-09

## 题面

出站连接池在夜里连续告警：活动连接数慢慢爬过上限，重启进程才回落；某次下游抖动后归还的连接带着半开状态又被借出去，下一笔请求直接超时；等待队列满了以后新来的借用既没报错也没超时，调用方线程一直挂着。范围是 gsb/batch49-py-safety-d-09 下的 pooled_conn_governor.py，类 PooledConnGovernor，负责每个目标地址的连接池容量、空闲复用、健康校验、租约回收与等待队列。

公开接口：PooledConnGovernor()；configure(target, *, max_size=4, max_idle=2, max_requests=100, max_lifetime_s=300, idle_ttl_s=60, lease_ttl_s=30, max_waiters=4, wait_timeout_s=5)、acquire(target, now, owner="")、poll_waiter(waiter_id, now)、release(conn_id, healthy=True, now=None)、discard(conn_id, reason="manual")、reap(now)、leak_report()、stats()、snapshot()、restore(state)。连接用 ConnView(conn_id, target, owner, requests, created_at, leased_since) 表示。拒绝时抛 PoolError(code, detail)，错误码只用 POOL_INVALID_CONFIG、POOL_UNKNOWN_TARGET、POOL_UNKNOWN_CONN、POOL_QUEUE_FULL、POOL_WAIT_TIMEOUT、POOL_SNAPSHOT_INVALID。

需要做到的行为：

1. configure 校验 max_size 为正、0 < max_idle <= max_size、max_requests 为正、idle_ttl_s 与 lease_ttl_s 与 wait_timeout_s 为正、max_waiters 不为负；不合法抛 POOL_INVALID_CONFIG 且不留下半配置。返回包含 target 与全部限额的字典。
2. acquire 遇到未知目标抛 POOL_UNKNOWN_TARGET；优先复用空闲连接，结果 status="acquired"、reused=True、requests 加一，并重新记录租约起点。返回结果含 status、conn、reused、waiter_id、position 五个字段。
3. 没有空闲且连接数未到 max_size 时新建连接，conn_id 唯一，owner、created_at、leased_since 与 requests=1 都要填。
4. release 校验连接存在，未知连接抛 POOL_UNKNOWN_CONN；健康且 requests 与存活时长都在限内则转空闲，outcome="idle"、promoted 为 None；now 缺省时取当前 UTC 时间。discard 关闭指定连接并计入 closed，未知连接返回 False 而不抛错。
5. 归还时 requests 超限、存活时长超限或健康检查失败都要关闭，outcome="closed"，reason 分别用 max_requests、max_lifetime、unhealthy，并从池里移除。
6. 借出连接按 lease_ttl_s 记租约；reap 关闭空闲超过 idle_ttl_s 的连接并回收租约到期的借出连接，返回 idle_closed、leases_reclaimed、leaks 三个计数。leak_report 返回当前租约已超期但尚未回收的借出连接清单，正常状态下为空列表。
7. 借出后调用方崩溃未归还的连接在租约到期后必须回收并计入泄漏统计，不能永久占着名额。
8. 归还的连接健康检查失败时必须关闭并让等待者重新排队，不能直接交给下一个借用者。
9. 池满且还有等待额时 acquire 返回 status="queued"、waiter_id、position，队首 position 为 1，waiter_id 从 w1 开始递增，队列按先来后到。排队不占用连接名额，队首被服务之前其他借用者不能插队。
10. 池满且等待队列也满时新借用必须快速失败，抛 POOL_QUEUE_FULL，detail 带 retry_after_s，不得阻塞调用方。
11. poll_waiter 返回只含 status 与 conn 两个键的字典：在超时前 status=waiting，超过 wait_timeout_s 返回 timeout 并移出队列；已超时的 waiter 再查抛 POOL_WAIT_TIMEOUT。健康归还且有人等待时直接把连接交给队首，outcome="handed_off"，promoted 为 waiter_id。
12. stats 固定返回 targets、connections、idle、leased、waiters、acquired、reused、closed、reclaimed、leaks 十个键；snapshot/restore 保留目标配置、连接、等待队列与计数，快照带 version=1，非法结构抛 POOL_SNAPSHOT_INVALID 且原状态不变。

硬约束：acquire、release、reap、poll_waiter 共用同一把可重入锁，并发下不得超发连接或让同一连接被借给两个调用方；快照恢复后空闲表、租约、等待队列与计数完整复原，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 15 条全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: pooled_conn_governor
- typeName: PooledConnGovernor
