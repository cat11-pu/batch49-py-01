# batch49-py-safety-c-14

## 题面

身份同步上线后，SCIM 组补丁成了新的故障源：同一条 add 被网关重放，组里出现重复成员；remove 的过滤条件写错，接口报 500 导致整批同步卡住；分页对账跑到一半源端删了人，收尾时又把已删成员补了回来。灰度环境里还有一次把 remove 的成员过滤写成空列表，接口直接把整组成员清空，回滚花了两个小时。更危险的是嵌套组被接成环，展开成员时递归不返回。范围是 gsb/batch49-py-safety-c-14 的 scim_group_patch_reconciler.py，类 ScimGroupPatchReconciler，负责组补丁、成员上限、嵌套校验与分页对账。

公开接口：ScimGroupPatchReconciler(*, max_members=100, max_depth=3, max_pages=50)；create_group(group_id)；apply_patch(*, group_id, operations)；get_group(group_id)；list_members(group_id, *, expand=False)；begin_reconcile(group_id)；reconcile_page(*, token, page, members)；finish_reconcile(token)；stats、snapshot、restore。拒绝抛 ScimPatchError(code, detail)。

故障与边界：①新建组版本号为 1，重复建组报 SCIM_GROUP_DUPLICATE，查询不存在的组返回 None，补丁打到不存在的组报 SCIM_GROUP_NOT_FOUND；②add 去重后按字典序返回新增成员，全部已存在时 changed 为 False 且版本不变；③remove 的 value 可以是 id 列表，也可以是 {"filter": {"type": "user"}} 或 {"filter": {"type": "group"}}；④remove 的过滤条件匹配不到任何成员时必须返回成功且版本不变，不能报错也不能加版本；⑤replace 用新集合整体替换，版本只加一；⑥op 非法、path 不是 members、value 形状不对统一报 SCIM_INVALID_PATCH，且整次补丁原子失败；⑦直接成员数超过 max_members 报 SCIM_MEMBER_LIMIT，不得部分写入，成员上限按直接成员计算，子组本身也算一个成员；⑧嵌套组只能引用已存在的组，深度按叶子为 1 计算，父组深度要连同受影响的祖先一起重算，超过 max_depth 报 SCIM_NESTING_DEPTH；⑨嵌套组形成环时必须拒绝该次补丁并保持原成员集合不变；⑩list_members 默认返回直接成员，expand=True 递归展开用户并去重，子组 id 不出现，所有输出按字典序；⑪分页对账从 page 0 开始且必须连续，令牌按 rec-0001 递增，乱序报 SCIM_PAGE_OUT_OF_ORDER，未知令牌报 SCIM_RECONCILE_NOT_FOUND，空白成员报 SCIM_INVALID_PAGE，页数超过 max_pages 报 SCIM_TOO_MANY_PAGES；⑫对账期间发生的本地补丁必须按版本重放而不是覆盖，每个本地补丁各加一版，收尾时集合若有变化再加一版；分页对账期间源端删除了中间页成员时最终集合要收敛到源端，不能把已删成员补回，对账只覆盖用户成员，已有的嵌套组关系要保留；快照深拷贝，顶层字段固定为 version、groups、stats，恢复结构不符抛 SCIM_SNAPSHOT_INVALID 且原状态不动。

硬约束：同一组的补丁与对账必须串行，版本号只能单调加一，成员上限与嵌套深度在并发下不得被突破；对账收尾与本地补丁并发时不能丢版本，令牌一次性使用，收尾后立即失效，不能重复收尾；每次补丁结果要能看出本次新增与移除的成员，快照恢复后成员、嵌套关系、版本与全部计数一致。stats 返回 groups、patches、changed、reconciled、reconcile_pages、members、nested_links，其中 patches 统计未抛错的补丁调用数，changed 只统计真正改动成员集合的补丁数，reconciled 为完成的收尾次数，reconcile_pages 为已接收分页总数，members 为各组直接成员总数，nested_links 为当前子组条目总数。

验收：`python3 -m pytest -q tests` 共 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 身份认证
- type: Bugfix
- module: scim_group_patch_reconciler
- typeName: ScimGroupPatchReconciler
