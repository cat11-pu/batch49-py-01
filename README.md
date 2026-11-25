# batch49-py-safety-b-15

## 题面

模糊测试的种子库上个月被审计问住。一条内存越界崩溃样本，问它是从哪条种子变异来的，工具只给出一条断掉的链——中间那层被清理脚本删过，删完之后整棵子树都查不到根。同一段内容被三条不同的变异路径各自产出，图里却挂了三个节点，去重统计直接翻了三倍。还有一次 havoc 阶段在一个节点下面挂了两百多个子节点，导出的关系图打不开，工具也没留下任何截断标记。团队要求把血缘追踪、删除占位和分叉上限收进一个组件，历史查询必须一直能查到根。现在的关系表说白了就是一张边表加一个邻接字典，清理脚本一跑就把中间节点整行删掉，留下来的孤儿节点只能靠人工翻日志才串得起来。

改动范围限定在 gsb/batch49-py-safety-b-15 的 fuzz_seed_lineage.py，类型 FuzzSeedLineage。对外接口：FuzzSeedLineage(*, max_depth=64, max_children=8)；register_root(seed_id, data)；mutate(parent_id, child_id, data, mutation)；node(seed_id)；ancestors(seed_id)；descendants(seed_id)；roots()；delete(seed_id)；lineage_for(seed_id)；truncated_children(seed_id)；merge(other_state)；snapshot()；restore(state)；stats()。LineageNode 用 dataclass 承载 node_id、digest、parent_id、mutation、depth、deleted。

要修的点：

① register_root 生成深度为 0 的根节点，digest 由内容算出，mutation 固定记 root。
② seed_id 重复报 LINEAGE_DUPLICATE，空 id 或空内容报 LINEAGE_INVALID。
③ mutate 把子节点挂到父节点下，depth 为父深度加一，mutation 记录变异方式；父节点不存在报 LINEAGE_UNKNOWN，子 id 重复报 LINEAGE_DUPLICATE。
④ 内容相同但来源不同的语料合并为同一个节点，保留多条来源记录，不新增节点。
⑤ 深度超过 max_depth 的变异报 LINEAGE_DEPTH_EXCEEDED，不落图。
⑥ 子节点数触及 max_children 后继续分叉只记摘要并把该分支标记截断，不静默丢子树，truncated_children 按序列出被截断的摘要，摘要里带父节点、子 id 与变异方式，方便事后按摘要补录。
⑦ ancestors 从根到父依次返回，根节点返回空元组；descendants 返回全部后代并按 id 升序。
⑧ 删除语料只打删除标记并保留血缘占位，其后代仍要能一路追溯到根，不能断链。
⑨ delete 幂等：已删除或根本不存在都返回 False，不报错；被删节点的 parent_id 保持原值，继续充当后代追溯的中间跳板。
⑩ merge 以 node_id 为主键合并另一个实例的快照，只计真正新增的节点数，重复合并返回 0。
⑪ snapshot 与 restore 之后节点、父子关系、删除标记、截断摘要、累计统计都要在；lineage_for 返回祖先链再加自身，恢复后的实例可以直接继续 mutate。

硬约束：max_depth 与 max_children 是硬边界，任何一条路径都不得让图无限膨胀；ancestors、descendants、roots 的输出顺序只由节点 id 决定，同一张图任意次查询结果一致；快照要能跨会话恢复并用 merge 合并，合并本身必须幂等，同名节点以先到的为准，不覆盖已有的父子关系。stats 返回 nodes、roots、mutations、deleted 四个累计计数。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，不改测试、不跳过、不按固定输入写死；删除占位与跨会话合并自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: fuzz_seed_lineage
- typeName: FuzzSeedLineage
