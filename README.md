# batch49-py-game-b-08

## 题面

合批之后室内场景的抽屉和桌腿串成了一整块，碰撞体还在原位，材质槽却掉了一个；大件模型一旦顶点数超过索引位宽，整批直接黑掉，重新导入又恢复正常。美术在编辑器里数过一遍：同一个房间的静态物件两百八十个，合批后本该只剩几个批次，实际输出四十七个，里面还夹着两个不含任何顶点的空批。范围是 gsb/batch49-py-game-b-08 的 static_batch_builder.py，类 StaticBatchBuilder，负责把静态网格按材质与光照贴图分组、合并顶点与索引、重算包围盒，并在超过 16 位索引上限时拆批。拒收统一抛 BatchError(code, detail)，code 是稳定字符串。

公开签名：

- StaticBatchBuilder(*, max_vertices_per_batch=65535, max_batches=8, max_groups=16)
- group_key(material, lightmap) -> str
- add_mesh(mesh_id, *, material, lightmap, vertices, indices) -> str
- remove_mesh(mesh_id) -> int
- build() -> tuple
- split_overflow(group_key) -> tuple
- bounds(batch_id) -> tuple
- invalidate(group_key=None) -> None
- snapshot() -> dict 与 restore(state) -> None
- stats() -> dict

要修的地方：

1. group_key 用 material 与 lightmap 拼成 material|lightmap；两者为空或全空白报 BATCH_INVALID_ARG。
2. 顶点序列为空报 BATCH_EMPTY_MESH；索引越界报 BATCH_BAD_INDEX；顶点不是三分量数值报 BATCH_BAD_VERTEX。
3. add_mesh 返回分组键；分组数受 max_groups 限制，新建分组超限报 BATCH_GROUP_LIMIT。
4. build 按分组键升序输出，批号形如 group_key#序号，序号从 0 起。
5. 合并按入库顺序拼接顶点，索引整体后移已合并的顶点数，不得交叉重排。
6. remove_mesh 按引用减一，返回剩余引用总数；未知网格报 BATCH_UNKNOWN_MESH；引用清零的分组不再产出批次。
7. 单批顶点数受 max_vertices_per_batch 限制，超出时按顶点个数依次切块拆批，序号连续。
8. 总批次数受 max_batches 限制，超限报 BATCH_LIMIT_EXCEEDED，并且保留上一次成功构建的结果。
9. build 只重建脏分组，未失效的分组复用上次结果；首次构建重建全部分组。stats 的 builds 记构建次数，rebuilt_groups 记累计重建组数，dirty 记当前脏组数。
10. invalidate 不带参数时把全部分组标脏，带未知分组报 BATCH_UNKNOWN_GROUP。
11. 同一网格被两个分组引用时引用计数各自独立，移出一侧不得让另一侧丢几何；拆分后的子批必须保持原批的材质与光照贴图，索引偏移按子批自己的顶点基址重算，不能沿用原批偏移。
12. 包围盒要覆盖拆分后每个子批的实际顶点，空批不产出也不占批次上限；bounds 只接受最近一次构建产出的批号，其余报 BATCH_UNKNOWN_BATCH。

硬约束：add_mesh、remove_mesh、invalidate 与 build 会从资源加载线程和渲染线程并发进入，脏位推进与整批替换必须在同一把锁内完成，读方拿到的只能是完整旧批或完整新批，不能看到半套顶点；分组的数据结构与统计计数也不得读到中间态。snapshot 要导出网格表、引用计数、分组内容、脏位与全部计数，restore 之后重建结果与计数一致；状态结构不合法时报 BATCH_SNAPSHOT_INVALID，原状态保持不动。分组数、总批次数与单批顶点数都是硬顶，不得超发。

验收：python3 -m pytest -q tests 15 条全过；测试不得修改、跳过或按固定输入写死；并发重建与快照恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: static_batch_builder
- typeName: StaticBatchBuilder
