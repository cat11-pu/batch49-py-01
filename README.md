# batch49-py-game-b-13

## 题面

切到索引色素材后，天空渐变出现一圈圈色阶，角色边缘的透明像素被填成黑块，同一帧连续量化两次结果还会不一样；渲染组要求把调色板和误差扩散一起修。美术还发现同一条渐变在 8 位与 16 色调色板下断阶位置不一致，怀疑切分点选取没有固定顺序，转场时整帧颜色还会轻微左右跳。改动范围限定在 gsb/batch49-py-game-b-13 的 palette_quantizer.py，类型 PaletteQuantizer，公开签名保持不变：PaletteQuantizer(*, capacity=16, dither_strength=0.75, max_pixels_per_frame=4096)；add_color(rgb, count=1)；histogram()；build_palette()；palette()；nearest_index(rgb)；remap_frame(pixels, width)；dither(pixels, width)；quantize(pixels, width)；stats()；snapshot()；restore(state)。错误一律抛 PaletteError(code, detail)。

需要修好的行为：

1. add_color 的 rgb 必须是三个 0 到 255 的整数、count 必须是正整数，否则分别报 PALETTE_INVALID_COLOR 与 PALETTE_INVALID_COUNT，同一颜色累加计数，histogram() 返回以 rgb 元组为键、计数为值的副本；
2. 调色板容量包含 0 号透明槽，实际代表色至多 capacity-1 个；中位切分每次都选总计数最大的盒子，按最长通道排序后从累计计数首次达到一半的位置切开，无法再切的盒子跳过，盒子总数达到 capacity-1 就停止；
3. 盒子代表色为按计数加权平均后四舍五入，代表色按 (r,g,b) 升序排进调色板，重复代表色只保留一个，四舍五入按加 0.5 后取整处理；
4. palette() 在直方图变化后重算，未变化时返回同一份结果，连续两次重算逐项一致；
5. nearest_index 从 1 号槽开始按欧氏距离平方找最近项，距离相同取较小索引；
6. remap_frame 接收行优先像素列表与宽度，None 表示透明，宽度必须为正且整除长度，否则报 PALETTE_INVALID_FRAME；像素数超过 max_pixels_per_frame 报 PALETTE_FRAME_LIMIT，宽度为 1 时每行只有一个像素，折返逻辑仍要成立；
7. dither 使用蛇形 Floyd–Steinberg，偶数行从左到右、奇数行从右到左，量化误差乘 dither_strength 后按 7/16、3/16、5/16、1/16 分配给右、左下、下、右下四个邻居，误差分配前先把累加值按上下界钳制，钳制差额单独累计；
8. quantize 在 dither_strength 为 0 时与 remap_frame 结果一致，否则走 dither，两条路径都按行优先顺序消费像素，返回长度与输入一致；
9. stats 返回 frames、colors、pixels、palette_size、discarded_error 五个计数，frames 每量化一帧加一，pixels 累计处理过的像素数；
10. snapshot 返回的字典包含 version、capacity、dither_strength、max_pixels_per_frame、histogram、palette、counters，version 固定为 1，restore 遇到结构不符报 PALETTE_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：颜色数不足容量时调色板不补齐空槽，索引从 0 连续，透明索引始终占用 0 号槽；中位切分遇到多条颜色计数相同必须按颜色值升序选切分点，保证跨进程一致；误差扩散在左右边界折返时不得越出当前行，钳制掉的误差单独计入 discarded_error；同一批帧重复量化结果稳定；调色板容量、扩散强度与每帧像素预算都有上限；同一调色板在同一输入上重复使用必须给出相同索引，调色板重建后旧缓存不得复用。

验收：python3 -m pytest -q tests 15 条全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: palette_quantizer
- typeName: PaletteQuantizer
