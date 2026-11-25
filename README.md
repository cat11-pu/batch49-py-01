# batch49-py-fix-e-03

## 题面

产线物联网平台复核发现：一个只有 plant/line1 采集范围的账号，能订阅 plant 全量并收到别家温度；运维补了拒绝规则，已订阅客户端重放保留消息时照样把 plant/secret/token 的旧值吐出来；另一个现场把共享订阅前缀写成 $share 加两个斜杠，启动后整组订阅静默失效。追查下来，规则匹配、通配符覆盖与保留消息回放各有一套判断，互不认账，队列里还留着按旧规则投递的消息。入口在 gsb/batch49-py-fix-e-03 的 mqtt_topic_acl.py，类 MqttTopicAcl，负责主题与过滤器规范化、规则台账、发布与订阅授权、投递队列以及保留消息回放隔离。

公开入口：MqttTopicAcl(*, max_rules=64, max_depth=8, max_subscriptions=8, max_queued=8, max_retained=32, default_effect="deny")；normalize_topic(topic)；normalize_filter(flt)；add_rule(principal, flt, *, effect="allow", priority=0)；rules()；authorize(principal, flt, *, action="subscribe")；subscribe(principal, flt, *, qos=0, session="default", now=0)；unsubscribe(principal, flt)；publish(principal, topic, payload, *, qos=0, retain=False, now=0)；replay(principal, *, session="default", now=0)；drain(principal, *, session="default", limit=None)；subscriptions()；revoke(principal)；snapshot()；restore(state)；stats()。拒绝统一抛 MqttAclError(code, detail)。

要落地的行为：①normalize_topic 拒绝空串、加号与井号、空白及控制字符；按斜杠切分不得出现空层（首尾或连续斜杠），层数不超过 max_depth；美元符号只在首层开头；合规主题原样返回，违规报 MQTT_TOPIC_INVALID。②normalize_filter 中加号必须独占一层且不能与其他字符合并，井号必须独占最后一层且只能出现一次；空层、首尾斜杠、层数超过 max_depth 报 MQTT_FILTER_INVALID。③共享订阅过滤器为 $share/<group>/<filter>，group 只允许字母、数字、连字符与下划线，长度 1 到 32，内层过滤器不得再以 $share 开头；规范化后原样返回带前缀形式，违规报 MQTT_FILTER_INVALID；同一共享组内按消息序号轮转，一条消息只投给一个订阅者。④add_rule 主体非空，effect 只允许 allow 或 deny（否则报 MQTT_RULE_INVALID），priority 为整数；同一主体、过滤器、效果与优先级重复提交返回已有条目且 created 为假，不占容量；规则数超过 max_rules 报 MQTT_CAPACITY_EXCEEDED；rules 按加入顺序返回 id、principal、filter、effect、priority、created。⑤authorize 取主体精确匹配或星号主体的规则，按优先级高者胜、过滤器更具体者胜（层数多、通配符少）、主体精确者胜、仍相同 deny 胜的顺序裁决；未命中按 default_effect，reason 为 default、rule 为 None，命中 reason 为 matched，返回 allowed、rule、reason、effect。⑥过滤器覆盖：规则的井号覆盖任意后续层，也覆盖父级主题本身；加号只精确覆盖一层；根级通配符不得匹配以 $SYS 开头的系统主题；subscribe 按覆盖关系判断请求过滤器，publish 与 read 按具体主题匹配。⑦subscribe 先授权，拒绝报 MQTT_SUBSCRIBE_DENIED；qos 只允许 0、1、2（否则报 MQTT_QOS_INVALID）；同一主体、过滤器与会话重复订阅返回 created 为假并保留原 qos 与 id；单主体订阅数超过 max_subscriptions 报 MQTT_CAPACITY_EXCEEDED；订阅记录含 id、principal、filter、qos、session、since。⑧publish 先授权，拒绝报 MQTT_PUBLISH_DENIED 且序列号不前进；允许时序列号自增，按过滤器匹配投进每个订阅队列，投递 qos 取两者较小值，队列超过 max_queued 丢最旧的并累计 dropped；返回 seq、delivered、dropped、retained。⑨retain 为真时每个主题只留最新一条，超过 max_retained 丢最早写入的主题；replay 按主题升序返回有匹配订阅且当前可 read 的保留消息，qos 取较小值、retained 为真，并累计 replays；规则收紧后 replay 必须按当前 ACL 重判，不能沿用订阅时的授权。⑩drain 按 seq 升序取出该会话队列消息，limit 限制条数；消息含 principal、topic、payload、qos、seq、retained。⑪revoke 删除该主体的全部规则、订阅与队列，返回 rules、subscriptions、queued 计数，之后授权按默认结论处理并累计 revocations；unsubscribe 删除主体与规范化过滤器对应订阅，首次返回真、不存在返回假。⑫snapshot 深拷贝规则、订阅、队列、保留消息、序列号、计数与配置；restore 结构不符报 MQTT_SNAPSHOT_INVALID 且原状态可用；stats 至少给出 rules、subscriptions、publishes、delivered、denied_publish、denied_subscribe、dropped、replays、revocations、retained。

硬约束：add_rule、subscribe、unsubscribe、publish、drain、revoke 与快照写入必须在同一把可重入锁内完成；多个线程同时发布时序列号唯一且连续，同一队列不得重复投递或乱序。容量判定先于写状态，触及上限不得留下半套条目；快照与恢复整体替换台账，恢复失败不得破坏原状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: mqtt_topic_acl
- typeName: MqttTopicAcl
