# batch49-py-fix-b-03

## 题面

跨机房联调时，网关侧的抓包记录里出现了几行不该存在的东西：请求头值里夹着换行，换行后面紧跟一条伪造的内部签名；同一个 authorization 键在一条请求里出现了两次，取值一真一假；还有调用方把 grpc-encoding 改成自定义值，下游按原样转发，日志里于是出现了解析不了的编码。值班同学一直按框架默认行为放过这些请求，直到某次权限判断把 x-internal-role 当成可信输入，才有人意识到入站元数据从来没有被当成不可信数据处理。入口在 gsb/batch49-py-fix-b-03 的 grpc_metadata_gate.py，类 GrpcMetadataGate，负责元数据规范化、方法级鉴权、截止时间解析与出站白名单。

公开入口：GrpcMetadataGate(token_verifier=None, *, max_pairs=32, max_key_bytes=64, max_value_bytes=1024, max_methods=64, trusted_peers=(), propagate_keys=('x-request-id', 'traceparent', 'x-tenant'))；register_method(method, *, audiences, roles=('user',), max_deadline_ms=30000, required_headers=())；set_trusted_peers(peers)；methods()；revoke_method(method)；policy(method)；sanitize(metadata)；authorize(method, metadata, *, peer, now=0)；snapshot()；restore(state)；stats()。拒绝统一抛 GrpcMetadataError(code, detail)。

要落地的行为：①metadata 是 (key, value) 二元组序列，key 去空白后转小写；含非 ASCII 字符、非法字符或以非字母数字开头给 GRPC_METADATA_INVALID。②值必须是字符串，出现回车、换行、NUL 或其它控制字符一律给 GRPC_METADATA_INVALID。③authorization、grpc-timeout、x-request-id、traceparent、x-tenant 属于单值键，重复出现给 GRPC_METADATA_INVALID；其它键重复时按出现顺序合并成元组。④键数超过 max_pairs、键长超过 max_key_bytes、值长超过 max_value_bytes 给 GRPC_METADATA_LIMIT，长度一律按 UTF-8 字节数计算而不是字符数。⑤以冒号开头的伪头部、grpc- 前缀键（grpc-timeout 除外）、以及 te、connection、transfer-encoding 这类逐跳头必须静默剔除并记入 stripped，不得因此报错。⑥以 -bin 结尾的键按 base64 解码后放进 binary，多值按出现顺序保留且允许无填充写法，解码失败给 GRPC_BINARY_INVALID 并整条请求拒绝，sanitize 返回 pairs、binary、stripped、count，count 为读入的键值对总数。⑦方法策略：方法名必须是斜杠开头的 包名.服务/方法 三段形式；重复登记给 GRPC_METHOD_EXISTS，未登记的调用给 GRPC_METHOD_UNKNOWN，登记数量超过 max_methods 给 GRPC_CAPACITY_EXCEEDED；策略至少含 audiences、roles、max_deadline_ms、required_headers，policy 返回该方法的策略副本，未登记给 GRPC_METHOD_UNKNOWN，register_method 返回 method、audiences、roles、max_deadline_ms 与 required_headers。⑧鉴权：authorization 缺失或不是 Bearer 前缀给 GRPC_UNAUTHENTICATED；token 按策略 audiences 的顺序交给 token_verifier(token, audience)，全部返回空给 GRPC_TOKEN_INVALID；claim 里的 role 不在策略 roles 内给 GRPC_ROLE_DENIED。⑨grpc-timeout 必填，按 H、M、S、m、u、n 单位解析成毫秒并向上取整、最小 1 毫秒；缺失给 GRPC_DEADLINE_MISSING，非法或非正给 GRPC_DEADLINE_INVALID；超过策略 max_deadline_ms 时钳到上限并把 clamped 置真。⑩x-internal- 开头的键只有可信 peer 能携带：不可信 peer 带来时剔除并计入 spoofed，可信 peer 保留但永不外传。⑪出站 outbound 只保留 propagate_keys 白名单内的键，authorization 与 x-internal- 永不透传；返回体含 method、subject、audience、role、deadline_ms、clamped、stripped、outbound、peer。⑫snapshot 深拷贝方法策略、可信 peer 集合与计数，返回 methods、trusted_peers、stats 三个键，restore 结构不符给 GRPC_SNAPSHOT_INVALID 且原状态可用；stats 至少含 calls、allowed、denied、stripped、spoofed，calls 每次进入 authorize 计一次，denied 累计被拒调用。

硬约束：规范化、策略查表、鉴权与计数必须在同一把可重入锁内完成，同一方法被并发登记时只能有一个请求成功，其余必须拿到 GRPC_METHOD_EXISTS，并且不得留下只写了一半的策略条目。快照与恢复整体替换策略表、可信集合与计数，恢复失败不得留下半套状态；从快照恢复后，快照之后新增的方法与累计计数都不得残留。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: grpc_metadata_gate
- typeName: GrpcMetadataGate
