# batch49-py-game-b-10

## 题面

角色走进雪地场景后，边缘忽粗忽细：法线转折处描了一整圈，深度台阶处又描一遍，同一条轮廓叠出双线；镜头一推近，描边越过 tile 边界被切掉，屏幕边上留下断口。范围是 gsb/batch49-py-game-b-10 的 outline_pass_planner.py，类 OutlinePassPlanner，负责按深度与法线差分判定物体边缘、按视距缩放线宽、给每帧的 tile 采样排预算。拒收统一抛 OutlineError(code, detail)，code 是稳定字符串。

公开签名：

- OutlinePassPlanner(*, grid=(8, 8), max_width=6, max_samples_per_frame=1024)
- set_thresholds(*, depth, normal) -> dict
- thresholds() -> dict
- detect_edge(depth_delta, normal_dot) -> int
- width_for_depth(depth, base_width) -> int
- pixel_width(depth, base_width, depth_hit, normal_hit) -> int
- plan_tiles(width, tiles) -> tuple
- set_participation(*, transparent=False, masked=False) -> None
- composite_order(objects) -> tuple
- budget() -> dict
- snapshot() -> dict 与 restore(state) -> None
- stats() -> dict

要修的地方：

1. set_thresholds 一次替换深度阈值与法线阈值并返回新阈值副本；深度阈值必须为正、法线阈值必须落在 (0, 1] 内，否则报 OUTLINE_BAD_THRESHOLD，原阈值保持不变。thresholds 返回副本，调用方改返回值不影响内部。
2. detect_edge 用 abs(depth_delta) 严格大于深度阈值判深度边（返回位 1），用 normal_dot 小于法线阈值判法线边（返回位 2），两条都不命中返回 0；传入非数值或 NaN 报 OUTLINE_INVALID_SAMPLE。
3. 线宽公式是 min(max_width, max(1, int(base_width * 10.0 / depth)))，int 向零截断；depth 小于等于 0 报 OUTLINE_BAD_DEPTH，base_width 小于等于 0 报 OUTLINE_BAD_WIDTH。
4. pixel_width 在只有深度边命中时取 depth 线宽，两种边都不命中返回 0；同一像素同时命中深度边与法线边时只描一次，线宽取两者较大值，不能叠加变粗。
5. plan_tiles 按 (2*width+1)^2-1 计算每个内部 tile 的采样数，返回 TilePlan 元组，字段为 tile、samples、trimmed；width 超出 1 到 max_width 报 OUTLINE_BAD_WIDTH。
6. tile 坐标必须落在 grid 内，越界报 OUTLINE_BAD_TILE。
7. 每帧采样数受 max_samples_per_frame 限制，本批会把累计用量推过上限时报 OUTLINE_BUDGET_EXCEEDED，本次一个 tile 都不提交、计数不动；budget 返回 limit、used、remaining 三个值。
8. 描边宽度超过 tile 边界时按实际可用邻域截断，trimmed 置真，跨 tile 的线不得出现断口。
9. 透明物体不写深度但仍需参与法线判定时，按配置只读法线通道，不因深度缺失被误判为边缘。
10. set_participation 配置透明物体与遮罩物体是否参与描边；composite_order 按提交顺序返回参与描边的对象 id，未参与的直接跳过；对象缺字段、id 为空或字段类型不对报 OUTLINE_INVALID_OBJECT。
11. 描边在透明合成之前写入目标层，参与对象按提交顺序稳定输出，重复调用结果一致。
12. stats 返回 edge_calls、edge_hits、tiles_planned、samples_used 四个计数，前两个是 detect_edge 的调用数与命中数，后两个是已排计划与已用采样数。

硬约束：set_thresholds、plan_tiles 与 detect_edge 会从渲染线程和材质热重载回调并发进入，阈值与参与配置必须是整份替换，读方要么看到完整旧参数、要么看到完整新参数，不能读到半套；采样预算的记账与 tile 计划的提交必须在同一把锁内完成，同一帧不得超发。snapshot 要导出当前阈值、参与配置、已用采样与全部计数，restore 之后数值一致；状态结构不合法时报 OUTLINE_SNAPSHOT_INVALID，原状态保持不动。线宽上限、每帧采样数与 tile 网格都是硬顶。

验收：python3 -m pytest -q tests 15 条全过；测试不得修改、跳过或按固定输入写死；并发更新参数与快照恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: outline_pass_planner
- typeName: OutlinePassPlanner
