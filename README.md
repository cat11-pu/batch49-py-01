# batch49-py-game-b-02

## 题面

换季 UI 重新打图集之后，图标边缘出现邻居图标的颜色，缩到二级 mip 更明显，个别槽位四角还发黑。图集构建器本来要按槽位向外复制一圈像素当隔离带，这次改动把这段逻辑弄丢了。第一批出问题的是背包格子的九宫格图标，边缘一圈偏绿，缩放到 0.5 后四个角直接变黑，重打一次图集能好一阵，换一批图又复发。落点在 gsb/batch49-py-game-b-02 的 atlas_gutter_builder.py，类 AtlasGutterBuilder，负责页内槽位登记、gutter 规划、mip 级构建与脏区增量。

公开接口：AtlasGutterBuilder(page_width, page_height, *, max_gutter=8, max_mip_levels=4, budget_bytes=1048576)；add_slot(slot_id, x, y, width, height)；slots() 升序返回槽位 id；slot(slot_id)；plan_gutters(declared, slot_ids=None) 返回按槽位 id 升序的 GutterPlan；build_level(level) 返回 MipLevel；invalidate_region(slot_id, x, y, width, height) 返回合并后的脏矩形数；dirty_rects()；budget()；snapshot()、restore(state)；stats()。拒绝时抛 AtlasGutterError(code, detail)，码为 ATLAS_ 前缀。

要修的点：①槽位必须落在页内且宽高为正，id 不得为空，越界或非正尺寸拒绝；②槽位之间不允许重叠，同 id 重复登记拒绝；③gutter 声明宽度只接受 1 到 max_gutter 的整数，越界或非整数拒绝；④每个槽位产出四边条带加四个角块共 8 个矩形，条带长度与槽位对应边一致，角块是边长等于 gutter 的正方形，边角处允许条带与角块覆盖同一块像素但规划里必须各自列出；槽位贴页边时 gutter 只向页内扩，越界部分丢弃且不得绕到页另一侧取样；相邻槽位间距小于两倍 gutter 时按半间距收缩，GutterPlan 的 actual 记录收缩后的实际宽度，declared 保持调用方声明值，采样表仍按声明宽度对齐；⑤只规划请求的槽位，未知槽位 id 在规划阶段报错；⑥mip 每升一级 gutter 减半向下取整，最低保留 1 像素，层级从 0 开始，超出 max_mip_levels 拒绝；⑦同一层级重复构建命中缓存不重复计费，脏区失效后才允许重建；⑧脏矩形按槽位合并，相交或相邻的合成一个，dirty_rects 按稳定顺序返回，越出槽位范围的区域拒绝；⑨gutter 字节数按矩形面积乘 4 累计，超过 budget_bytes 拒绝该层级，budget() 给出上限、已用与层级数；⑩快照深拷贝，恢复后槽位、规划、层级、脏区与计数都要回来，非法快照报 ATLAS_SNAPSHOT_INVALID 且不改当前状态；⑪stats 要能给出 slots、planned、levels、builds、invalidations、dirty；⑫带 alpha 的槽位四角按最近角复制像素，alpha 一起复制，不得用全透明像素补齐。

补充口径：gutter 矩形必须完整落在页内，规划结果按槽位 id 升序返回，保证同一输入多次规划逐字段一致；脏区坐标相对槽位左上角记录，合并后换算到页坐标再输出；mip 层级的字节数按本层全部 gutter 矩形面积合计。

硬约束：登记、规划与构建可能被多个线程同时触发，槽位表、规划缓存与脏区都要在锁内维护，不能出现半更新的规划；进程重启后凭快照恢复，已构建层级与脏区不得丢失，重建只针对脏区涉及的部分；槽位数量、gutter 宽度、mip 级数与字节预算都是硬上限，超限报错而不是截断或静默丢弃。

验收口径：`python3 -m pytest -q tests` 的 15 条用例全部通过，测试不得改动、跳过或按固定输入写死；并发、重启与预算上限自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: atlas_gutter_builder
- typeName: AtlasGutterBuilder
