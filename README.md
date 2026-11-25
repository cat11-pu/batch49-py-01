# batch49-py-game-b-09

## 题面

背包格子刷新一次，批次数量就涨一截：同一张图集里的图标被裁切框切成好几段，遮罩里的物品反倒跟底图合到了一起；列表往下滚时，层级更高的提示条被后合的格子压住，直接看不见了。范围是 gsb/batch49-py-game-b-09 的 ui_batch_composer.py，类 UiBatchComposer，负责按绘制顺序把 UI 四边形合成批次，同一纹理页、同一裁剪区与同一遮罩才合并。拒收统一抛 UiError(code, detail)，code 是稳定字符串。

公开签名：

- UiBatchComposer(*, max_quads_per_batch=4, max_pages=8, max_batches=8)
- push_quad(element_id, *, page, layer, rect, clip=None, mask=None) -> None
- push_clip(element_id, *, page, layer, rect, clip) -> None
- push_mask(element_id, *, page, layer, rect, clip=None, mask_op="") -> None
- flush() -> tuple
- batches() -> tuple
- pending_elements() -> tuple
- reorder(element_id, new_layer) -> None
- snapshot() -> dict 与 restore(state) -> None
- stats() -> dict

要修的地方：

1. push_quad 校验 element_id 非空、page 非空、layer 为非负整数、rect 为四元组且宽高为正、clip 为 None 或四元组且宽高为正、mask 为 None 或非空字符串；违反任一项报 UI_INVALID_ARG，且该元素不得入队。
2. push_clip 的 clip 必填，push_mask 的 mask_op 必填；缺失、None 或空串报 UI_INVALID_ARG。
3. 入队只登记，flush 才合成；batches 在首次 flush 之前返回空元组，pending_elements 按提交顺序返回尚未合成的元素 id。
4. 合成前按 layer 升序稳定排序，同 layer 保持提交顺序；层级高的元素必须排在后面，不能被后合批越过。
5. 连续元素 page、clip、mask 三者完全一致才并入同一批；换纹理页、换裁剪矩形、换遮罩都断批。
6. 单批四边形数受 max_quads_per_batch 限制，达到上限后在下一条元素前断批，批号按 b0、b1、b2 递增。
7. 一次合成里出现的不同纹理页数受 max_pages 限制，超限报 UI_PAGE_LIMIT；累计总批次数受 max_batches 限制，超限报 UI_BATCH_LIMIT。两种报错都不得提交半份结果，待合成元素原样保留、计数不变。
8. flush 返回累计批次，重复 flush 只把新入队的元素合成到后面的批号上，已提交的批次不改写。
9. reorder 只改尚未 flush 元素的 layer，按新层级重排；元素不存在或已经提交报 UI_UNKNOWN_ELEMENT。
10. 裁剪区只扩大不缩小的连续元素可以共批，裁剪区一旦收窄必须断批，即使纹理页相同。
11. 同一元素重复提交且 id 相同时按最后一次覆盖原位置，不新增四边形，也不改变层级顺序；批次达到四边形上限时优先在下一个同页元素前断开，不得把同一元素的四边形拆到两批。
12. stats 返回 pending、quads、batches、flushes 四个计数，pending 是尚未合成的元素数，quads 是已提交的四边形总数。

硬约束：push 与 flush 会从 UI 线程和异步加载回调并发进入，入队、排序、合成必须在同一把锁内完成，读方拿到的只能是完整的旧批次集合或完整的新批次集合，不能看到半套元素；batches 返回独立副本，调用方修改返回值不得影响内部状态。snapshot 要导出待合成元素、已提交批次与全部计数，restore 之后合成结果与计数一致；状态结构不合法时报 UI_SNAPSHOT_INVALID，原状态保持不动。单批四边形数、纹理页数与总批次数都是硬顶，不得超发。

验收：python3 -m pytest -q tests 15 条全过；测试不得修改、跳过或按固定输入写死；并发入队与快照恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: ui_batch_composer
- typeName: UiBatchComposer
