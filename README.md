# batch49-py-fix-b-05

## 题面

一次容量复盘会上，网关组把最近三个月的连接日志摊开，发现两条曲线对不上：前端统计的并发流峰值只有八百，边缘节点却记录到三千多次流级取消，而且集中在几十个连接上。复盘结论写成一句话：客户端在应答返回之前连续发出大量取消帧，服务端每收到一个就开一个新流、再立刻销毁，连接数没涨，CPU 和内存却被这种来回拉扯吃满。现有防护只按连接数限流，对单个连接内部的流生命周期完全没有账本。入口在 gsb/batch49-py-fix-b-05 的 h2_reset_flood_guard.py，类 H2ResetFloodGuard，负责连接与流的状态账本、取消事件滑窗、滥用判定与隔离动作。

公开入口：H2ResetFloodGuard(*, max_concurrent_streams=100, window_seconds=60, reset_limit=20, ratio_limit=0.5, min_samples=10, quarantine_seconds=30, max_connections=64, max_reset_history=2048)；open_connection(conn_id, *, now=0)；close_connection(conn_id, *, now=0)；open_stream(conn_id, stream_id, *, now=0)；close_stream(conn_id, stream_id, *, now=0)；record_reset(conn_id, stream_id, *, now=0)；evaluate(conn_id, *, now=0)；quarantine(conn_id, *, now=0, reason="manual")；release(conn_id, *, now=0)；state(conn_id, *, now=0)；prune(*, now=0)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 H2GuardError(code, detail)。

要落地的行为：①连接标识去空白后不得为空，否则 H2_CONN_INVALID；重复 open 同一标识给 H2_CONN_EXISTS；同时存在的连接数达到 max_connections 后新连接给 H2_CONNECTION_LIMIT；close_connection 对未知连接给 H2_CONN_UNKNOWN，对已关闭连接返回 False。②stream_id 必须是正奇数且在同一连接内严格递增，偶数、非正数或不超过 last_stream_id 的值一律 H2_STREAM_ID_INVALID，且不得改变连接内任何计数。③处于 open 状态的流数达到 max_concurrent_streams 后新流给 H2_CONCURRENCY_EXCEEDED；关闭任一旧流后可以继续开通，并发账本随之回落；因超限被拒的请求不得计入 open 过的流数。④close_stream 只对处于 open 的流返回 True，重复关闭或从未打开的流返回 False；正常关闭不计入取消事件。⑤record_reset 把流置为 closed 并记录一次取消；对已经被正常关闭的流再次收到取消仍要计数，返回值里的 reset_count 是窗口内累计数。⑥取消一个从未 open 过的 stream_id 给 H2_STREAM_UNKNOWN，本次不得写入取消事件，并且该连接要按协议违例立即进入隔离，不等阈值。⑦取消事件按 window_seconds 滑动过期，evaluate 与 record_reset 的 reset_count 只统计 now 减事件时间不超过窗口的事件；prune 删除所有连接上的过期事件并返回条数。⑧窗口内取消数达到 reset_limit 且样本数达到 min_samples 时 level 为 storm、action 为 GOAWAY，并把连接隔离 quarantine_seconds 秒；隔离期内 open_stream 给 H2_QUARANTINED。⑨未达风暴但窗口内 open 过的流数达到 min_samples、且取消数与 open 数之比不低于 ratio_limit 时，level 为 suspicious、action 为 MONITOR，此判定不隔离。⑩隔离期内重复 evaluate 返回 level 为 quarantined、action 为 QUARANTINED，不得重复累计隔离次数；隔离期间到达的迟到取消仍要计入 resets，但不得延长隔离到期时间；隔离到期后 state 的 quarantined 必须为 False 且可以继续开流；release 对未被隔离的连接返回 False。⑪quarantine 立即把 until 设为 now 加 quarantine_seconds，返回 action 为 GOAWAY 与 until，reason 只作记录。⑫snapshot 深拷贝连接表、取消事件与计数；restore 结构不符给 H2_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 connections_opened、streams_opened、resets、quarantines、refused、pruned。

硬约束：建连、开关流、记取消与判定必须在同一把可重入锁内完成，判定与隔离要作为一个原子步骤，阈值满足时多个线程同时 evaluate 只能有一个动作是 GOAWAY，其余必须是 QUARANTINED，quarantines 计数只能加一。快照与恢复整体替换连接表、事件与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后新开的流与新增的取消都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: h2_reset_flood_guard
- typeName: H2ResetFloodGuard
