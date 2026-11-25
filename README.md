# batch49-py-game-b-15

## 题面

室内关卡隔着墙能看到隔壁房间的灯，玩家贴着门缝走开时整间屋子的可见物件骤然全黑，走廊两端门洞交汇处还会漏出走廊外的场景。美术在门洞里侧摆了一盏灯，门开到一半时整间屋子的可见物件还是会整批消失，门完全打开才恢复，怀疑开度被当成开关处理。改动范围限定在 gsb/batch49-py-game-b-15 的 portal_visibility_solver.py，类型 PortalVisibilitySolver，公开签名保持不变：PortalVisibilitySolver(*, max_depth=4, max_portals=64, clip_budget=256)；add_cell(cell_id, x0, y0, x1, y1)；add_portal(portal_id, cell_a, cell_b, axis, position, lo, hi)；set_camera_cell(cell_id)；set_eye(x, y)；open_portal(portal_id)；close_portal(portal_id)；set_door_fraction(portal_id, fraction)；solve()；visible_cells()；visible_region(cell_id)；visible_area(cell_id)；visible_objects(objects)；budget_used()；snapshot()；restore(state)。错误一律抛 PortalError(code, detail)。

需要修好的行为：

1. add_cell 要求 cell_id 非空、x0<x1、y0<y1，重复 id 报 PORTAL_CELL_EXISTS，非法矩形报 PORTAL_INVALID_CELL；
2. add_portal 要求两个单元都已存在（否则 PORTAL_UNKNOWN_CELL）、axis 为 "v" 或 "h"、lo<hi（否则 PORTAL_INVALID_PORTAL）、portal_id 不重复（PORTAL_PORTAL_EXISTS），门户总数超过 max_portals 报 PORTAL_LIMIT；
3. 门状态默认未知，按完全打开处理；open_portal 置为全开，close_portal 置为全关，set_door_fraction 接受 0 到 1 的开度，越界报 PORTAL_INVALID_FRACTION，门户不存在时统一报 PORTAL_UNKNOWN_PORTAL；
4. 门洞按开度线性收窄，两端各收 (1-开度)/2 的长度，开度为 1 时保持原门洞，开度为 0 时门洞长度收为零；
5. solve 以相机所在单元为起点，visible_cells 返回发现顺序，起点单元的区域就是它的矩形本身，不叠加任何门洞带，未设相机报 PORTAL_NO_CAMERA，set_camera_cell 传入未知单元报 PORTAL_UNKNOWN_CELL；
6. 竖门只约束 y 区间、横门只约束 x 区间，子单元区域等于子单元矩形与所有已穿过门洞带的交集；
7. 传播到深度 max_depth 的单元记录后不再继续穿过门户；每次门洞穿越消耗一次裁剪预算，预算用完立即停止，budget_used 返回已用次数，深度从 0 开始计，穿越一扇门后子单元深度加一，同一单元上的门户按 portal_id 升序尝试；
8. 同一单元只记录首次到达的区域并只传播一次，环路不得重复加入，后续路径只用于确认是否已经访问；
9. visible_region 返回矩形元组、不可见返回 None，visible_area 返回面积，visible_objects 接收 (object_id, cell_id) 列表并按可见单元过滤，返回按 id 升序的对象名，对象列表允许为空；
10. snapshot 返回的字典包含 version、cells、portals、camera、eye、regions、order、budget，其中 cells 是 cell_id 到 (x0,y0,x1,y1) 的映射，portals 是 portal_id 到 (cell_a, cell_b, axis, position, lo, hi, 开度) 的映射，regions 是 cell_id 到矩形，order 是上次求解的单元顺序，budget 是已用裁剪次数，regions 与 order 反映最近一次求解结果，未求解时为空，version 固定为 1；restore 遇到结构不符报 PORTAL_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：门开度在 0 到 1 之间时可见区域按开度线性收窄，不能按全开或全关二值化；同一单元从两条路径到达时可见区域取并集且只传播一次，避免指数级重复；相机位于两个单元共用的门户平面上时两个单元都作为起点，不得因归属不明丢可见集；求解与门状态更新并发时只读取整份门状态；传播深度、门户数与每帧裁剪预算都有上限硬顶；门状态更新与求解都要在锁内完成整份读取，不能一边遍历一边改。

验收：python3 -m pytest -q tests 15 条全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: portal_visibility_solver
- typeName: PortalVisibilitySolver
