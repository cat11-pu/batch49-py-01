# batch49-py-safety-a-14

## 题面

能力位映射最近把两个团队都拖下水：内核上报了一个注册表里没有的位，解码直接抛异常，整条采集链路停了；配置里有人手写了 unknown:5 这类占位名，编码时被当成未知位，权限全部被拒；旧版本导出的位表在新机器上恢复，位号 12 已经被重新分配给另一个名称，恢复后两台机器的权限对不上，排查了三天；集合运算一边按名称算、一边按位算，结果差了两个能力；第 63 位在有的机器上变成负数，掩码整体错位；现在每台机器各存一份名称表，值守同学每周手工核对一次，还是对不上。这次收进 gsb/batch49-py-safety-a-14 的 capability_name_registry.py，类 CapabilityNameRegistry 维护名称、别名、位号三张表，异常统一为 CapRegistryError(code, detail)，支持结构版本常量为 SUPPORTED_SCHEMA=1。

公开接口：CapabilityNameRegistry(*, max_name_length=32, max_aliases=8)；register(name, *, bit, aliases=())；unregister(name)；encode(names)；decode(mask)；set_ops(op, left, right)；resolve(name)；aliases(name)；table()；version()；snapshot()；restore(state)。

要求逐条落地：①位号必须是 0 到 63 的整数，第 63 位按无符号处理，越界报 BIT_RANGE，非整数报 INVALID_REQUEST；②名称与别名都必须非空、不含空白、长度不超过 max_name_length，违规报 INVALID_REQUEST 或 NAME_LIMIT，别名数超 max_aliases 报 ALIAS_LIMIT；③同名重复注册报 NAME_TAKEN，同一位号被另一名称占用报 BIT_TAKEN，别名与已有名称或别名冲突报 ALIAS_TAKEN，失败一律不改注册表；④版本初始为 1，每次成功注册或注销加一，table() 返回 version、names、aliases、bits 四键的深拷贝；⑤encode 接受名称与别名，多个名称按位或，遇未注册名称一次列出全部未知项（升序元组）且不部分编码；⑥decode 按位号升序输出规范名，未注册位输出 unknown:位号，掩码为负或超出 64 位报 MASK_RANGE，decode 后再 encode 必须得回原位掩码，别名不得出现在 decode 结果里；⑦set_ops 支持 and、or、xor、diff，名称层与位层结果一致，未知操作报 INVALID_OP，未知名称报 UNKNOWN_NAME；⑧快照含 schema、version、names、aliases，恢复后版本取当前值与快照值的较大者保证单调，table 与 snapshot 都返回深拷贝；⑨快照结构非法（版本小于 1、位号越界、同位重复、别名指向不存在的名称）报 SNAPSHOT_INVALID 且不动现场，schema 高于支持版本报 SNAPSHOT_VERSION_UNSUPPORTED；⑩注册名称恰好是 unknown:位号 这样的字面量时，encode 必须按已注册名称处理，不能当成未知位；⑪旧快照里的位号在新注册表里已经分配给另一个名称，恢复要报 SNAPSHOT_CONFLICT，不能静默改名；⑫空集合与全 64 位的序列化形式必须能区分，不能共用一个哨兵值。

硬约束：并发 encode 与 decode 不得看到半更新，table 与 snapshot 取到的一定是自洽快照；集合运算在名称层与位层必须可对账；名称长度、别名数、位号范围都是硬顶。

验收：`python3 -m pytest -q tests` 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: capability_name_registry
- typeName: CapabilityNameRegistry
