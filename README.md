# batch49-py-game-b-03

## 题面

图集热更新上线后，切场景第一帧角色身上会闪一下旧贴图，极端情况下整块材质变成另一张图的内容。排查方向是新一代页构建完成与正在渲染的帧之间没有隔开，旧代页还没等到在飞帧结束就被回收。第一批复现的是一套角色皮肤，切换后披风的 UV 落在上一代的页上，同一帧里脸用新代、披风用旧代。要改的是 gsb/batch49-py-game-b-03 的 atlas_generation_swapper.py，类 AtlasGenerationSwapper，管代际构建、UV 重映射、在飞帧钉扎与整代回滚。

公开接口：AtlasGenerationSwapper(*, max_pages_per_generation=8, max_pins=16)；begin_generation(generation)；stage_page(generation, page_id)；stage_uv(generation, slot_id, page_id, u0, v0, u1, v1)；commit_generation(generation) 返回代序号；pin(generation=None) 返回句柄；unpin(handle) 返回该代剩余钉扎数；active_generation()；lookup(slot_id, generation=None) 返回 UVMapping；collect() 返回回收的页 id；rollback() 返回回退后的代 id；generations()；snapshot()、restore(state)；stats()。拒绝时抛 AtlasGenerationError(code, detail)，码为 ATLAS_ 前缀。

要修的点：①代 id 非空且不得重复，提交前只能追加页与 UV；②每代页数上限 max_pages_per_generation，同页重复登记与超额都拒绝；③UV 必须落在 0 到 1 之间且左下小于右上，页必须已登记在该代，槽位 id 非空；④提交要求该代至少一页且至少一条 UV，否则整代作废并保持旧代生效；⑤提交必须原子，成功后代序号加一、active_generation 立即指向新代，失败时旧代映射与代列表原样不动；⑥已提交的代不允许再追加页或 UV；⑦lookup 默认解析当前代，指定代时按该代映射解析，未知槽位与未知代分别报错；⑧pin 记录在飞帧，unpin 按句柄归还并返回剩余计数，句柄重复归还拒绝，未归还钉扎总数不得超过 max_pins；⑨collect 只回收已退役且无钉扎的代的页，重复调用返回空；⑩rollback 整代回退到上一代并丢弃当前代，没有上一代时报错；⑪快照覆盖代列表、当前代、历史、钉扎与计数并深拷贝，恢复后 lookup 与 active_generation 一致，非法快照报 ATLAS_SNAPSHOT_INVALID；⑫stats 给出 begun、pages_staged、uv_staged、commits、pins、unpins、collected_pages、rollbacks、outstanding_pins、generations。

补充口径：代序号从 1 开始单调递增，回退后代号不回收；页 id 在同一代内唯一，跨代允许复用；UV 重映射按槽位 id 索引，同一槽位在一代内只能有一条映射，重复登记拒绝。

硬约束：切换窗口内新请求只能落到新代，已钉扎的旧代帧继续按旧映射解析，两代映射并存且互不覆盖；提交后立刻回退时，被新代复用的页要么恢复旧内容要么整代作废，不允许半新半旧；同一槽位在新代被删除后旧代钉扎仍然有效，钉扎归零才允许回收对应页；构建、提交、钉扎与回收可能被多个线程同时调用，状态必须在锁内整体替换；页数与钉扎数都是硬上限，超限报错，不做静默淘汰。

验收口径：`python3 -m pytest -q tests` 的 15 条用例全部通过，测试不得改动、跳过或按固定输入写死；并发切换、回滚与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: atlas_generation_swapper
- typeName: AtlasGenerationSwapper
