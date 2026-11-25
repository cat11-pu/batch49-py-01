# batch49-py-safety-d-06

## 题面

线上客服系统这周连着报了三类故障：运营在后台撤掉某个直播间话题的订阅授权后，前端仍然间歇收到该话题的推送；被撤权的连接重新点订阅，页面直接白屏没有任何提示；还有客户反馈同一个话题退订一次以后仍旧收到消息。范围是 gsb/batch49-py-safety-d-06 下的 ws_subscription_authorizer.py，类 WsSubscriptionAuthorizer，负责每条 WebSocket 连接的订阅集合、话题授权、待发队列与撤销传播。

公开接口：WsSubscriptionAuthorizer(*, max_connections=256, max_topics_per_conn=32, max_outbox_per_conn=64, resubscribe_cooldown_s=30, max_resubscribe_attempts=3)；connect(conn_id)、disconnect(conn_id)、grant(conn_id, topic)、subscribe(conn_id, topic, now=None)、unsubscribe(conn_id, topic)、revoke_grant(topic, now=None)、broadcast(topic, payload, now=None)、flush_outbox(conn_id)、stats()、snapshot()、restore(state)。拒绝时抛 WsAuthError(code, detail)，错误码只用 WS_INVALID_REQUEST、WS_CONNECTION_LIMIT、WS_UNKNOWN_CONNECTION、WS_SUBSCRIPTION_LIMIT、WS_TOPIC_NOT_ALLOWED、WS_RESUBSCRIBE_COOLDOWN、WS_RESUBSCRIBE_EXHAUSTED、WS_SNAPSHOT_INVALID。

需要做到的行为：

1. connect 登记连接，同 id 重复 connect 不重复计数，连接数到 max_connections 再连抛 WS_CONNECTION_LIMIT；disconnect 清掉该连接的全部订阅与待发消息并返回清除的订阅数，未知连接返回 0。
2. grant 只登记授权不建订阅；subscribe 之前没有授权一律 WS_TOPIC_NOT_ALLOWED，未知连接 WS_UNKNOWN_CONNECTION，连接标识或话题为空串 WS_INVALID_REQUEST。
3. subscribe 返回 SubscriptionView(conn_id, topic, subscribed_at)，订阅时间取传入的 now，缺省用当前 UTC 时间；每连接订阅数到 max_topics_per_conn 抛 WS_SUBSCRIPTION_LIMIT；unsubscribe 返回布尔，未订阅时返回 False 且不改计数。
4. 同一连接对同一话题重复订阅只保留一条记录，退订一次就完全解除，不留残余条目。
5. broadcast 只投递给该话题的订阅者并返回投递条数，无人订阅返回 0 且不产生任何队列项；每连接待发队列到 max_outbox_per_conn 时丢弃最旧一条并计入 broadcast_dropped，不能无界增长；投递计数只统计真正入队的消息。
6. 待发队列按单调递增的 seq 编号，flush_outbox 按入队顺序返回并从队列移除，再次 flush 得到空列表。
7. revoke_grant 解除该话题在所有连接上的授权与订阅，向受影响连接各投递一条 kind="notice" 的撤销通知，返回字典只含 revoked_connections、removed_subscriptions、notices 三个键；对无订阅话题撤销是幂等空操作，不产生通知。
8. 撤销发生在广播排队之后时，队列里属于该话题的未发消息必须一并清掉，只停新消息是不够的。
9. 撤权后重新订阅要重新鉴权，并受冷却与次数上限约束：冷却期内即使授权已经恢复也必须拒绝，错误 detail 带上 retry_after_s；重试次数用完抛 WS_RESUBSCRIBE_EXHAUSTED。
10. stats 固定返回 connections、subscriptions、grants、outbox_items、broadcast_delivered、broadcast_dropped、revoked_subscriptions 七个键，计数与实际状态一致。
11. snapshot 深拷贝连接、授权、订阅、队列与计数，快照带 version=1，改动快照不能影响在线状态；restore 校验结构，非法结构抛 WS_SNAPSHOT_INVALID 且原状态保持不变。
12. 授权撤销与广播并发时撤销必须先完成订阅解除；broadcast 与 flush_outbox 并发不得丢项、不得重复投递。

硬约束：所有状态变更持同一把可重入锁，队列 seq 单调不重复；restore 之后连接、授权、订阅、待发消息与计数完整复原，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 15 条全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: ws_subscription_authorizer
- typeName: WsSubscriptionAuthorizer
