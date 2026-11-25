# batch49-py-game-b-01

## 题面

美术组本周导入一批 30000×20000 的关卡贴图，超过单张纹理上限，导入器改成按 tile 切块后依次过算子，结果拼接处出现亮边和错位；取消一次再重跑时整张图从第一个块重算，同时有导出任务把内存打爆。第一批受影响的是一张四万像素的远景天空盒，重跑三次都在同一列出现接缝，工程同学只能手工裁图绕过，单张导入耗时从十几秒涨到几分钟。问题集中在 gsb/batch49-py-game-b-01 的 tile_image_pipeline.py，类 TileImagePipeline，负责分块计划、算子登记、逐块执行与中断续跑。

公开接口：TileImagePipeline(tile_width, tile_height, *, max_inflight=4, max_buffer_bytes=1048576, max_tiles=256)；register_stage(name, func, *, halo, edge_mode="clamp", cost_per_pixel=1) 返回阶段序号；stages() 返回按登记顺序的阶段名；plan_tiles(image_width, image_height) 返回按行优先排列的 TileSpec；enqueue(image_width, image_height) 入队并返回新增块数；run_tile(tile_id)、drain()；resume() 返回仍需处理的块数；tile_spec(tile_id)、tile_result(tile_id)；cancel()；snapshot()、restore(state)；stats()。拒绝统一抛 TilePipelineError(code, detail)，码为 TILE_ 前缀。

要修的点：①每块按 tile_width×tile_height 切，最后一行一列允许不足，tile_id 从 0 开始按行优先连续编号；②读取范围要按所有阶段 halo 之和向四周扩边，算子按登记顺序串行执行，阶段摘要与块摘要都要稳定；halo 之和大于块边长时，边界块按实际可用邻域收缩读取范围，产出仍要与整图处理一致；③边模式只接受 clamp、reflect、zero，其余拒绝；④halo 为负、阶段名为空、cost_per_pixel 为负、func 不可调用都拒绝登记；⑤同一尺寸重复 enqueue 幂等并返回 0，换成另一尺寸要报错而不是混两套计划；⑥块数超过 max_tiles 直接拒绝，不做截断；⑦同时在飞块数不超过 max_inflight，单块中间缓冲本身超过 max_buffer_bytes 直接报错，合计在飞字节超过上限时排队等待而不是丢弃该块；⑧drain 按入队顺序返回结果，重复执行同一块返回相同摘要且不重复计数；⑨cancel 丢弃尚未执行的块并计数，已完成结果保留；⑩快照必须深拷贝，恢复后已完成块不再重算，非法快照报 TILE_SNAPSHOT_INVALID 且不改当前状态；⑪stats 要能给出 stages、planned、computed、pending、peak_inflight、buffer_peak、cancelled；⑫同名阶段重复登记按最后一次配置生效，已在队列中的块不因这次登记改变阶段序号与摘要。

补充口径：块摘要按参与运算的阶段名、读取范围与像素数计算，同一块重复执行必须得到完全相同的摘要，阶段摘要与阶段一一对应；中间缓冲按读取范围每像素 4 字节估算，统计只算真正执行完的块，失败、取消与回队重试不计入 computed；同一尺寸只能存在一份计划，任何时刻不允许两套 tile_id 并存。

硬约束：同一实例的登记、入队、执行可能被多个线程同时调用，阶段表与队列要在锁内维护，不能出现半更新的计划；进程中断后凭快照续跑，已产出摘要的块不得重算，续跑结果与不中断时逐块一致；tile 总数、在飞块数与缓冲字节都是硬上限，超限报错而不是静默丢弃。

验收口径：`python3 -m pytest -q tests` 的 15 条用例全部通过，测试不得改动、跳过或按固定输入写死；并发、中断续跑与内存上限自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: tile_image_pipeline
- typeName: TileImagePipeline
