# batch49-py-safety-d-07

## 题面

网关升级多跳正向代理后，海外线路每半小时就有请求卡在 407：客户端自带的代理凭证被原样带到下一跳，第二跳直接拒绝；CONNECT 隧道建好以后上游还在反复加认证头，目标站点开始回 400；某跳代理连续故障被摘除后，流量切到备用跳，但第二天主跳恢复，调度器立刻切回去又炸一次。范围是 gsb/batch49-py-safety-d-07 下的 hop_auth_forwarder.py，类 HopAuthForwarder，管代理链顺序、逐跳认证方式、凭证引用与故障跳摘除。

公开接口：HopAuthForwarder(*, max_auth_failures=2)；set_chain(hops)、register_credential(ref, secret)、forward(request)、authenticate_hop(hop_name, now=None)、mark_response(hop_name, status, now=None)、failover(hop_name)、hop_status()、stats()、snapshot()、restore(state)。hops 是 [{"name", "scheme", "credential_ref"}]，按链序排列，第一个是主跳，其余为备用跳。拒绝时抛 HopAuthError(code, detail)，错误码只用 HOP_INVALID_REQUEST、HOP_UNKNOWN_HOP、HOP_CREDENTIAL_MISSING、HOP_CHAIN_EXHAUSTED、HOP_SNAPSHOT_INVALID。

需要做到的行为：

1. set_chain 校验：必须是非空序列，每跳 name、scheme、credential_ref 三个键齐全且为非空字符串，跳名唯一；不合法一律 HOP_INVALID_REQUEST，且不改动原链。hop_status 按链序返回 name、scheme、has_credential、active、status（ready 或 failed）。
2. register_credential 只在内存保存密钥，日志与错误信息不得出现密钥明文；同一引用再次登记时覆盖旧密钥并让旧密钥立即失效；forward 解析活动跳的 credential_ref，未注册抛 HOP_CREDENTIAL_MISSING。
3. forward 收到 request 后不得修改传入字典，先剥离客户端自带的 Proxy-Authorization，再按当前活动跳的 scheme 与密钥重新注入一条 Proxy-Authorization。
4. CONNECT 请求只做隧道建立，不注入 Proxy-Authorization；隧道建立后的普通请求同样不再注入。
5. 客户端自带的 Proxy-Authorization 绝不能透传到链尾目标服务器，每一跳都必须替换成本跳凭证。
6. forward 返回 hop、status、attempt、headers；成功后 forwards 计数加一，返回的 headers 保留其余自定义头，键名大小写不做规范化。
7. authenticate_hop 单独记录每跳认证尝试次数；未知跳抛 HOP_UNKNOWN_HOP，查询不得改变链状态。
8. mark_response 记录响应：同一跳第一次 407 返回只含 action、hop、attempts 三个键的字典，action=retry_auth 并让 retries 加一；连续第二次 407 达到 max_auth_failures 时摘除该跳、切到下一跳，返回含 action、hop、active 的字典且 action=failover。
9. 407 重试只重放认证动作，不能把请求体重复发送给目标服务器。
10. failover 显式摘除指定跳并切到其后的可用跳，返回新 active；后面没有可用跳抛 HOP_CHAIN_EXHAUSTED 且状态不变。
11. 备用跳接管后原跳的失败计数保留；原跳恢复后不得自动切回主跳，必须通过 set_chain 或显式重置才允许重新成为活动跳。
12. stats 固定返回 hops、forwards、auth_attempts、auth_failures、failovers、retries 六个键；snapshot 只保存 scheme 与 credential_ref，绝不包含密钥明文，快照带 version=1，restore 后按引用重新解析，非法结构抛 HOP_SNAPSHOT_INVALID 且原链不变。

硬约束：forward、mark_response、failover、set_chain 共用同一把可重入锁，同一请求的 407 重试不得与链变更交错；快照只存凭证引用，恢复到一个没有密钥的实例后转发必须报 HOP_CREDENTIAL_MISSING，不能把引用当密钥用。

验收：`python3 -m pytest -q tests` 15 条全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: hop_auth_forwarder
- typeName: HopAuthForwarder
