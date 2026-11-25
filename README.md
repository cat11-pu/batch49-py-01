# batch49-py-safety-d-13

## 题面

数据防泄漏平台这周拦下一批事故：业务把数据库导出成 CSV 改了后缀名，检查只看扩展名就放过去；另一个上传包里套了四层压缩，解到第三层就放弃校验，外层照样放行；还有一台机器在配额快满时连续上传，每次都在传输到一半才被判超限，留下十几份半截对象，清理脚本和账单都乱了。现在要把出站文件的形态准入与配额预留收成一个组件，落在 gsb/batch49-py-safety-d-13 的 egress_content_gate.py，类 EgressContentGate。

公开接口：EgressContentGate(*, window_ms=60000, default_window_bytes=10485760, max_nesting_depth=2, max_uncompressed_bytes=104857600, max_targets=32, max_reservations_per_tenant=64)；set_target_policy(target, *, allowed_types, max_bytes, archive_depth=None, archive_uncompressed_bytes=None)；target_policy(target)；set_tenant_quota(tenant, *, window_bytes, window_ms=None)；inspect(target, *, filename, content_type, magic, size_bytes, declared_types=(), archive_entries=())；reserve(tenant, target, *, size_bytes, now_ms)；commit(tenant, reservation_id, *, now_ms)；release(tenant, reservation_id, *, now_ms)；usage(tenant)；stats()；snapshot()；restore(state)。拒绝统一抛 EgressError(code, detail)。

要落地的故障与边界：①目标名与租户名去空白转小写，空串或非字符串抛 GATE_TARGET_INVALID 或 GATE_TENANT_INVALID；策略版本自一起，每次覆盖旧值并加一，允许类型去重后按字典序存；②允许类型为空、类型元素非字符串、体积上限非正、压缩深度为负抛 GATE_POLICY_INVALID，目标数超过 max_targets 抛 GATE_TARGET_LIMIT；③未登记目标一律套最严策略：只允许 text/plain、上限 1 MiB、嵌套深度与解压总量上限都为零；④inspect 先按魔数判真实类型再与声明对照；同一文件带多个 Content-Type 声明或魔数与声明不符时取最严判定并可拒绝；⑤声明类型不在允许集合内返回 deny，reasons 只含 type_not_allowed；体积超过策略上限返回 deny，reasons 只含 size_exceeded；⑥压缩条目按路径分隔符计算嵌套深度、按条目体积求和；深度超过策略值必须整包拒绝，不能只解到上限部分就放行外层；⑦解压后总量超过策略上限返回 deny，reasons 只含 archive_size_exceeded；⑧空文件名、空声明类型、负体积或非字节魔数抛 GATE_REQUEST_INVALID，不得写入任何策略状态；⑨reserve 在窗口内先占用字节，成功返回形如 rsv-<序号> 的预留号与过期时刻，待用与已用分账，配额不足抛 GATE_QUOTA_EXCEEDED 且不产生预留，预留条数超过上限抛 GATE_RESERVATION_LIMIT；⑩commit 把预留从待用转为已用，重复提交或过期预留号抛 GATE_RESERVATION_UNKNOWN；⑪release 释放预留并返回是否命中，重复释放返回假；预留后上传失败必须释放预留，不能把失败传输计入已用配额；⑫窗口按 window_ms 滚动，跨窗口后已用与待用清零、旧预留号作废；并发 reserve 必须在锁内判额，配额不得被穿透。

set_target_policy 与 target_policy 返回 target、allowed_types、max_bytes、archive_depth、archive_uncompressed_bytes、version，未登记目标返回 version 为零的最严策略而不报错；set_tenant_quota 返回 tenant、window_bytes、window_ms；inspect 返回 target、decision、sniffed_type、declared_type、size_bytes、reasons、policy_version，decision 只认 allow 与 deny；reserve 返回 reservation_id、tenant、target、size_bytes、created_ms、expires_ms；usage 返回 tenant、window_start_ms、window_ms、used_bytes、pending_bytes、reservations；commit 返回 reservation_id、tenant、committed_bytes、used_bytes、pending_bytes。

stats 固定返回 policies、inspections、allowed、denied、reserved、committed、released、quota_rejections 八个计数；snapshot 固定返回 version、policies、quotas、windows、counters 五个键，除 version 与计数外都是可变副本；restore 遇到非字典、版本不符、键缺失或类型不对一律抛 GATE_SNAPSHOT_INVALID 且现场状态不变。

硬约束：同一租户同一窗口的并发预留必须在锁内完成，十路并发预留在配额只够三份时只能成功三份；目标策略表、预留表与压缩条目都有硬顶；快照必须深拷贝，恢复后策略、配额窗口、预留与计数完整且不与原对象共享结构。

验收：python3 -m pytest -q tests 的 15 条用例必须全过；不得改测试、不得跳过、不得按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: egress_content_gate
- typeName: EgressContentGate
