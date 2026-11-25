# batch49-py-safety-a-05

## 题面

压测时一个父组被切出三份额度，加起来比父组还多，内核直接报写失败；另一个父组缩容时脚本按比例砍子组，业务被无声降速；还有一次父组缩容成功但子组额度没动，可用余量算成了负数，监控告警刷了一夜。范围是 gsb/batch49-py-safety-a-05 的 cgroup_quota_partitioner.py，类 CgroupQuotaPartitioner，在 cgroup 树内维护每层额度账本，父组把额度切给子组，子组删除或缩容时额度回到父组。

公开入口：CgroupQuotaPartitioner(*, max_depth=5, max_children=6)；create_root(path, quota, *, weight=100, operator="system", request_id="")；create_child(parent, name, quota, *, weight=100, operator="system", request_id="")；resize(path, quota, *, request_id="", operator="system")；release(path, *, request_id="", operator="system")；migrate(path, new_parent, *, request_id="", operator="system")；set_weight(path, weight, *, request_id="", operator="system")；available(path)；allocated(path)；arbitrate(path, demands)；node(path)；children(path)；journal()；snapshot()；restore(state)；reconcile(actual_paths)。拒绝抛 QuotaError(code, detail)。创建、扩容缩容与迁移都返回改动后的 QuotaNode，根 depth 为 0、子组加一，流水条目的字段为 request_id、operator、action、path、before、after。

要落地的行为：①子组额度之和不得超过父组当前额度，超了报 QUOTA_EXCEEDED，detail 的 available 给出可用余量、allocated 给出已分配，不写半套。②父组缩到低于已分配总和一律拒绝 QUOTA_SHRINK_REFUSED，detail 的 allocated 给出已分配总和，不许按比例砍；父组扩容只增加可用余量，不改写任何子组已分配值。③删除子组把额度交还父组并返回交还量，删除后该路径立即不可再分配，父组可用余量同步加回，重复删除幂等返回 0；还有子组的父组不许删，报 QUOTA_HAS_CHILDREN。④子组迁移时先向目标父组预留、再还源父组，成功后路径换到新父组下；目标余量不足则整次不动，报 QUOTA_EXCEEDED。⑤权重为负直接报 QUOTA_BAD_WEIGHT，权重为 0 允许存在但不参与超分仲裁、仲裁结果给 0；arbitrate 返回子组名到授予额度的映射，只按权重比例分，需求封顶，不做负数补偿。⑥逐层额度叠加，任何一层余量不足时深层创建整条失败。⑦层级深度与每层子组数受 max_depth、max_children 硬顶，越界报 QUOTA_DEPTH_LIMIT、QUOTA_CHILD_LIMIT，后者 detail 的 limit 给出上限。⑧每次变更写一条流水，含请求号、操作者、动作、路径与变更前后额度；同一请求号重放只生效一次，不重复记流水。⑨快照恢复前校验每层子组额度之和不超过父组，不守恒报 QUOTA_SNAPSHOT_INCONSISTENT 且当前账本不动，恢复失败后父子关系保持原样。snapshot 返回 nodes、journal、requests、released 四个键：nodes 每条形如 path、parent、quota、weight、depth。⑩子组在模块外被直接删除，账本仍记着额度，available 侧要能通过 reconcile(actual_paths) 对账发现孤儿额度。⑪父组额度为 0 时新建子组并分配 0 应允许，不产生可用额度，也不能因除零崩。⑫迁移子组时目标父组同时被缩容，先扣后还的顺序会造成瞬时超分，必须先预留再落地，不能先写后校。

硬约束：同一父组上的切分串行执行，并发创建不得超分；账本读方不得看到半更新，额度变更与流水可对账，流水按请求号去重，重放返回首次结果而不是按新参数重算。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发切分与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: cgroup_quota_partitioner
- typeName: CgroupQuotaPartitioner
