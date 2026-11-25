# batch49-py-game-b-06

## 题面

主视图切到广角之后，同一块岩壁在主视图里只需中档模型，反射视图却还要最高档，阴影视图又要最低档，三个诉求互相压不住，显存占用顶到上限，远处物体在主视图里来回跳档，一帧里能看到两次模型变化。范围是 gsb/batch49-py-game-b-06 的 multiview_lod_arbiter.py，类 MultiViewLodArbiter，负责把多个视图对同一对象的 LOD 诉求合并成一个驻留档位，并按帧结算升降档。拒收统一抛 LodError(code, detail)，code 是稳定字符串。

公开签名：

- MultiViewLodArbiter(*, max_views=8, max_lod=3, frame_upgrade_budget=2, cooldown_frames=2, hysteresis=1)
- register_view(view_id, priority=0) -> ViewSpec
- unregister_view(view_id) -> int
- set_view_priority(view_id, priority) -> None
- request(view_id, object_id, lod, frame) -> None
- arbitrate(object_id, frame) -> LodDecision
- arbitrate_frame(frame) -> dict
- resident_lod(object_id) -> int
- upgrade_budget(frame) -> int
- decision_log() -> tuple
- snapshot() -> dict 与 restore(state) -> None
- stats() -> dict

要修的地方：

1. 视图注册受 max_views 限制，超限报 LOD_VIEW_LIMIT；重复注册同一 id 一律报 LOD_DUPLICATE_VIEW，重复判定排在容量判定之前。
2. 未注册视图提交诉求或改优先级报 LOD_UNKNOWN_VIEW，且不计入 requests。
3. lod 必须是 0 到 max_lod 的整数，越界报 LOD_LOD_RANGE；frame 必须是大于等于 0 的整数，否则 LOD_BAD_FRAME。
4. 同一对象的多视图诉求取最高精度（lod 数值最小）作为 demanded；该帧没有诉求时 demanded 等于当前驻留档，决策仍要返回。
5. 从未出现过的对象，驻留档取 max_lod。
6. 升级按对象计费：一个对象一帧最多消耗 1 单位预算，与跨档数无关；预算不足的对象保持原档并把 deferred 置真。
7. 降级立即执行，不占预算，也不受冷却限制。
8. 同一帧里多个对象争预算时，按各对象诉求视图中的最高优先级降序结算，同优先级按 object_id 升序；arbitrate_frame 按这个顺序跑完并返回 {object_id: 结算后驻留档}。
9. 降级之后同一对象在 cooldown_frames 帧内不允许再升级，冷却期内提出的升级诉求记 deferred；冷却从降级那一帧算起，满 cooldown_frames 帧后放行。
10. 滞回：demanded 比驻留档差出的档数小于 hysteresis 时不降级，达到或超过才降。滞回只作用于降级。
11. 撤销视图时它此前拉高的档位不得立刻回落，必须等冷却期结束才允许降；两个视图诉求相差超过一档时按最高档驻留，升级预算按差值在视图间分摊，不允许单个视图独占整帧预算；同一对象同一帧先请求高档又被请求低档时，以提交顺序最后一次为准，该帧只记一次切换。

驻留档变化要能复盘：decision_log 按发生顺序返回每次决策的记录，stats 返回 views、objects、upgrades、downgrades、deferred、requests 六个计数。

硬约束：request、arbitrate、unregister_view 会从渲染线程和流式加载线程并发进入，合并与结算必须在同一把锁内完成，同一对象同一帧只允许一次升降档决策；预算按帧隔离记账，跨帧不得串账。snapshot 要导出视图表、驻留档、待结算诉求、冷却起点和计数器，restore 之后数值与计数一致；状态结构不合法时报 LOD_SNAPSHOT_INVALID，且原状态保持不动。视图数量与每帧升级预算都是硬顶，不得超发。

验收：python3 -m pytest -q tests 15 条全过；测试不得修改、跳过或按固定输入写死；并发结算与快照恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: multiview_lod_arbiter
- typeName: MultiViewLodArbiter
