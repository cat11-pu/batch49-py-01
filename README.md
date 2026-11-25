# batch49-py-safety-c-05

## 题面

网关自省接口改成批量之后，调用方陆续反馈三类情况：一次批量里混进一条超长令牌，整批 503，监控上全批都记成失败；某个调用方拿到了不属于它的字段，值是 null，前端据此判断“字段存在但为空”，等于变相泄露；还有 hint 传错的请求，返回的字段集合跟实际令牌类型对不上，取值全是空。范围是 gsb/batch49-py-safety-c-05 下的 introspection_batch_projector.py，类 IntrospectionBatchProjector，负责批量自省、hint 回退与按调用方版本化投影。

公开接口：IntrospectionBatchProjector(*, max_items=20, max_total_bytes=4096, max_token_length=512)；register_caller(caller, *, version=1) -> ProjectionPolicy；set_projection(caller, fields, *, version) -> ProjectionPolicy；projection_of(caller)；register_token(token, *, token_type, client_id, subject, scope, issued_at, expires_at, grace_until=0, family_id=None, amr=()) -> TokenRecord；token_of(token)；remove_token(token) -> bool；introspect_batch(caller, requests, *, now) -> BatchResult；stats() -> dict；snapshot()、restore(state)。now 一律为整数毫秒。拒绝抛 IntrospectionError(code, detail)，码为 INTROSPECT_INVALID_REQUEST、INTROSPECT_UNKNOWN_CALLER、INTROSPECT_STALE_VERSION、INTROSPECT_DUPLICATE_TOKEN、INTROSPECT_BATCH_TOO_LARGE、INTROSPECT_SNAPSHOT_INVALID。

需求与边界：
①构造参数 max_items、max_total_bytes、max_token_length 均须为正；register_caller 空调用方抛 INTROSPECT_INVALID_REQUEST，默认投影为 ("sub","scope")、版本 1，重复登记返回原策略。
②set_projection 只接受字段名序列，未知字段、空序列或非字符串抛 INTROSPECT_INVALID_REQUEST；version 必须严格大于当前版本，否则抛 INTROSPECT_STALE_VERSION 且策略不更新，成功后投影更新计数加一。
③register_token 的 token_type 只允许 access、refresh、id，token、client_id、subject 非空，expires_at 必须为正且不早于 issued_at，grace_until 为 0 或不得早于 expires_at；重复 token 抛 INTROSPECT_DUPLICATE_TOKEN，其它不合法抛 INTROSPECT_INVALID_REQUEST。
④三种类型的完整字段集：access 为 token_type、client_id、sub、scope、exp、iat、ttl；refresh 在此基础上加 family_id；id 为 token_type、client_id、sub、exp、iat、ttl、amr。
⑤introspect_batch 的每条请求可以是字符串，也可以是含 token 与 token_type_hint 的映射；结果按请求顺序返回，BatchResult 给出 results、count、failed 与 bytes_used，bytes_used 为所有请求令牌长度之和。
⑥批内条数超过 max_items 或总字节超过 max_total_bytes 时整批抛 INTROSPECT_BATCH_TOO_LARGE；单条长度超过 max_token_length 或 token 为空只影响该条，返回 {"active": false, "error": "token_too_long"} 或 {"active": false, "error": "invalid_request"} 并计入 failed。
⑦未知令牌返回 {"active": false}，不抛错也不计入 failed；已移除的令牌同样处理；过期且不在宽限期的令牌只回 {"active": false}，不投影任何字段。
⑧先按 token_type_hint 对应类型查找，未命中再回退全部类型，hint 缺失或不认识也走回退；发生回退计 fallbacks，字段集必须按命中令牌的实际类型取，不能套用 hint 的类型字段集。
⑨令牌在 expires_at 之前为 active，ttl 为 expires_at 减 now；已过 expires_at 但早于 grace_until 时仍为 active、ttl 固定为 0、exp 仍为原过期时刻，并计 grace_hits。
⑩投影只包含调用方策略里列出的字段；调用方无权读取的字段整体不出现，不能以 null 或空串占位；不同调用方对同一令牌看到不同字段。
⑪stats 固定返回 callers、tokens、batches、items、active、inactive、failed、fallbacks、grace_hits、projection_updates 十项，items 等于 active、inactive 与 failed 之和。
⑫snapshot 返回 version 与 tokens、callers、policies、stats 的深拷贝；restore 先整体校验再落盘，结构不合法抛 INTROSPECT_SNAPSHOT_INVALID 且现场不变。

硬约束：同一批内各条互不影响，一条超长或非法不能拖垮整批，也不能打乱其余条目的顺序与计数；投影字段集在读取时按策略版本解析，不能把权限外的键塞进结果；快照恢复后令牌、策略版本与计数必须一致。

验收：python3 -m pytest -q tests 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: introspection_batch_projector
- typeName: IntrospectionBatchProjector
