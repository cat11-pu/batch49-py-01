# batch49-py-safety-c-13

## 题面

月初的全局登出演练暴露了一串问题：用户点了退出，七个接入方里有三个还留着会话；重试逻辑没有上限，一个挂掉的接入方把队列塞满，其他接入方的登出全被拖住；票据过期后队列还在往外发请求，审计里出现了一批本不该存在的调用。值班同学只能逐台登录七个接入方后台手工清理，用户投诉说退出登录形同虚设。演练报告要求这次必须留下可核对的投递台账，任何一个接入方没收到都要能解释原因。范围是 gsb/batch49-py-safety-c-13 的 sso_logout_ticket_broker.py，类 SsoLogoutTicketBroker，负责全局登出票据的生成、按接入方投递、失败台账与过期降级。

公开接口：SsoLogoutTicketBroker(*, max_attempts=3, base_backoff=30, max_queue_per_rp=8, default_ttl=600)；issue_ticket(*, user, session_id, rps, now, ttl=None)；deliver(*, ticket_id, rp, now, success, reason=None)；retry_pending(now)；pending(rp=None)；ack(*, ticket_id, rp, now)；expire(now)；get_ticket(ticket_id)；stats、snapshot、restore。拒绝抛 SsoLogoutError(code, detail)，时间为整数秒。

故障与边界：①开票时接入方去重并按字典序保存，票据号按 logout-0001 递增，空用户、空会话、空列表与空名称统一报 LOGOUT_INVALID_REQUEST；②每张票据有有效期，默认 600 秒，边界时刻按已过期处理，票据状态只有 open 与 expired，全部投递完成也不自动改成其他状态，投递结果状态为 delivered、pending、duplicate；③同一票据对同一接入方只成功投递一次，重复投递返回 duplicate 且不增加尝试次数与外发计数；④单次失败进入待补投台账，首次退避 base_backoff 秒，之后每次翻倍，retry_pending 只返回已到点的条目并按票据号、接入方排序，pending 返回同一顺序的当前台账；⑤补投成功要把尝试次数累加并计入 retried，台账里对应条目删除；⑥某接入方连续失败达到 max_attempts 后必须标记放弃并保留最后失败原因，不能无限重试占满队列；⑦每个接入方的待补投队列有上限，队列满时拒绝新的待补投并报 LOGOUT_QUEUE_FULL，不得影响已有条目，被拒绝的这次不计入尝试次数与 failed；⑧票据过期后仍在队列里的投递只能转为记录，不得再产生任何外发动作，expire 返回本次关闭的票据号；⑨同一用户并发发起两次全局登出时，第二张票据必须复用未完成的投递台账而不是重投全部接入方；⑩ack 把已投递或待补投的接入方标记为外部确认，重复 ack 返回 False，未知票据或未知接入方分别报 LOGOUT_TICKET_NOT_FOUND 与 LOGOUT_RP_NOT_ON_TICKET；⑪一个接入方失败不能影响同票据其他接入方的投递结果与补投计划；⑫快照深拷贝，顶层字段固定为 version、tickets、deliveries、stats，恢复结构不符抛 LOGOUT_SNAPSHOT_INVALID 且原状态不动。

硬约束：同一票据的投递、补投与确认必须串行，同一接入方的待补投队列在并发下不得超限；票据过期与补投并发时不能出现先外发后过期的顺序；快照恢复后票据、投递台账、退避时间与全部计数一致。stats 返回 issued、delivered、failed、retried、acked、expired、abandoned、pending，其中 pending 为当前待补投台账条目数，expired 为已关闭票据数。

验收：`python3 -m pytest -q tests` 共 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: sso_logout_ticket_broker
- typeName: SsoLogoutTicketBroker
