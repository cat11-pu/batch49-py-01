# batch49-py-game-a-10

## 题面

大世界换成扫掠宽相位后，帧率在怪群附近掉到个位数：候选配对每帧重复上报，预算截断的配对下一帧又从头排队，实体下线后缓存里还留着它的标识，靠近的怪反而排在远处石头后面。物理组统计过，同屏三百个实体时每帧生成近五万条候选，其中九成在上一帧已经处理过。范围是 gsb/batch49-py-game-a-10 的 broadphase_sweep_pruner.py，类 BroadphaseSweepPruner，负责单轴排序扫描、增量重排与每帧配对预算。

公开接口：BroadphaseSweepPruner(budget=64)；upsert(entity_id, min_x, min_y, max_x, max_y)、remove(entity_id)、query_pairs()、set_budget(budget)、stats()、entity_count()、bounds(entity_id)、contains(entity_id)、reset_cache()、snapshot()、restore(state)。包围盒用 Bounds 记录下界与上界，字段为 min_x、min_y、max_x、max_y，配对返回 (小标识, 大标识) 元组列表。stats 键为 entities、candidates、reported、pending、cache、budget。budget 表示单帧最多返回的配对数量，候选与缓存的状态只在本实例内维护，不跨进程共享。拒绝操作抛 BroadphaseError(code, detail)。

故障与边界：①包围盒下界大于上界或标识为空，分别报 INVALID_BOUNDS、INVALID_ENTITY；②同一实体重复 upsert 只更新包围盒，不产生重复条目；③沿最小 x 排序扫描，x 区间重叠且 y 区间也重叠才算候选；④配对统一规范成 (小标识, 大标识)，插入顺序不同结果一致，同一帧内不得出现重复配对；⑤包围盒变化后走增量重排，已上报且未变化的配对不重复上报；⑥每帧上报数量不超过预算，超出部分留待后续帧；⑦被预算截断的配对下一帧优先补报，且不得重复上报；⑧接近的配对优先上报，距离相同按标识升序；⑨移除实体后其参与的所有配对与缓存条目一并清掉，不得留下悬挂标识；⑩set_budget 接受非负整数，缩小时不得丢弃已上报缓存；⑪stats 返回实体数、候选数、本帧上报数、待补报数与缓存条目数；⑫快照结构不合法报 INVALID_SNAPSHOT，恢复后实体、缓存与预算都要还原。

硬约束：每帧不得对所有候选重新全量排序，包围盒未变的实体只做增量重排，单帧工作量受实体数与预算双重约束；query_pairs 与 upsert、remove 可能来自不同线程，缓存更新要加锁，读方只能看到完整上一帧或完整本帧结果；跨帧缓存条目数不超过实体对数的上界，实体移除后缓存不得继续增长。预算小于待补报数量时按距离从近到远补，同距离再按标识排序，保证多帧之间的补报顺序稳定。

验收：python3 -m pytest -q tests 的 15 条用例全部通过，不得改测试、跳过或按固定输入写死；预算截断与快照恢复自行验证；只做本地实现，所有接口只依赖标准库。交付源码、测试输出与一页以内的配对口径说明，验收以仓库内测试文件为准。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: broadphase_sweep_pruner
- typeName: BroadphaseSweepPruner
