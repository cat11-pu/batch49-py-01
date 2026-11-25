# batch49-py-game-b-07

## 题面

地形从高空俯冲下来时，相邻块的 LOD 档位不同，接缝两侧的顶点位置对不上，山脊上先露出一条细缝，镜头继续下降又突然合上；同一条缝在不同帧里宽度跳变，远处的块看起来一直在抖。范围是 gsb/batch49-py-game-b-07 的 geomip_morph_guard.py，类 GeomipMorphGuard，负责把块级 LOD 切换变成按帧推进的顶点插值，并保证共享接缝顶点使用同一系数。拒收统一抛 MorphError(code, detail)，code 是稳定字符串。

公开签名：

- GeomipMorphGuard(*, max_blocks=64, max_level=4, morph_frames=4, step_limit=2, max_concurrent=4)
- register_block(block_id, *, vertices=(), level=0) -> BlockState
- set_block_level(block_id, level) -> BlockState
- block_state(block_id) -> BlockState
- morph_factor(block_id) -> float
- blend_positions(block_id, high, low) -> tuple
- seam_vertices(block_a, block_b) -> tuple
- common_morph_factor(block_ids) -> float
- advance(frames=1) -> dict
- unload_block(block_id) -> None
- reload_block(block_id, *, level) -> BlockState
- snapshot() -> dict 与 restore(state) -> None
- stats() -> dict

要修的地方：

1. 块注册受 max_blocks 限制，超限报 MORPH_BLOCK_LIMIT；重复注册同一 id 报 MORPH_DUPLICATE_BLOCK，重复判定排在容量判定之前。
2. 块 id 为空或顶点索引里出现负值报 MORPH_INVALID_ARG；档位必须是 0 到 max_level 的整数，注册与改档越界都报 MORPH_LEVEL_RANGE；改档只接受已注册块，未知块报 MORPH_UNKNOWN_BLOCK。
3. 目标档位与原档位相同时不启动渐变，morph_factor 保持 1.0，advance 不把它算进推进集合。
4. 启动渐变后 morph_factor 从 0.0 起，按 progress 除以 morph_frames 线性给出；advance 每调用一次最多推进 step_limit 步，frames 超过 step_limit 时按 step_limit 计。
5. 推进到 morph_frames 步时块落到目标档，morph_from 改写为目标档，morph_factor 恒为 1.0，completed 计数加一。
6. advance 的返回值只包含仍在渐变中的块，格式为 {block_id: 当前系数}；已完成的块不出现在返回值里。
7. blend_positions 按当前系数在 high 与 low 之间逐分量线性插值，系数 0 返回 high，系数 1 返回 low；两个序列长度不一致、或单个坐标的分量数不一致，报 MORPH_GEOMETRY_MISMATCH。
8. seam_vertices 返回两块共享顶点索引的升序元组，没有共享时返回空元组；任一块未注册报 MORPH_UNKNOWN_BLOCK。
9. 同时处于渐变中的块数受 max_concurrent 限制，超限报 MORPH_CONCURRENCY_LIMIT，被拒的块保持原档、不进入渐变。
10. 同一接缝顶点被三个块共享且三块处在不同渐变阶段时，必须取三者系数的上限，不能各算各的，否则接缝会裂开；块被卸载再重新加载后，系数要按当前可见状态重算，不得从 0 重新起算造成一次跳变。
11. morph_frames 为 0 时直接切到目标档，不产生中间帧，也不累计推进误差。
12. stats 返回 blocks、morphing、started、completed、advances 五个计数，morphing 是当前仍在渐变中的块数。

硬约束：set_block_level、advance 与 blend_positions 会从流式加载线程和渲染线程并发进入，档位变更、系数推进与并发计数必须在同一把锁内完成，同一块在同一帧只允许推进一次；接缝上多个块的系数取值必须一致，不能读到半新半旧的档位。snapshot 要导出块的档位、渐变起点、进度、可见状态与顶点索引，以及全部计数，restore 之后系数与计数一致；状态结构不合法时报 MORPH_SNAPSHOT_INVALID，原状态保持不动。单帧推进步数与并发渐变块数都是硬顶，不得超发。

验收：python3 -m pytest -q tests 15 条全过；测试不得修改、跳过或按固定输入写死；并发推进与快照恢复要自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: geomip_morph_guard
- typeName: GeomipMorphGuard
