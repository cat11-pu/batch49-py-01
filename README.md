# batch49-py-game-b-11

## 题面

夜景关卡换到宽银幕后，凡是镜头里有路灯的画面，整串光晕要么一起消失，要么在屏幕边缘被整段抹掉；美术对着录像逐帧比对，确认问题出在光晕合成这一层。同一盏路灯在 1080p 与 4K 下亮度对不上，切到宽银幕遮挡关系还会整体错位，美术已经按门板、立柱和屏幕边缘三种情形分别截图对比。改动范围限定在 gsb/batch49-py-game-b-11 的 lens_flare_composer.py，类型 LensFlareComposer，公开签名保持不变：LensFlareComposer(*, width=1920, height=1080, max_elements=8, max_samples=16, max_flares_per_frame=4)；size()；resize(width, height)；set_source(x, y, depth)；add_element(element_id, offset, weight, radius)；remove_element(element_id)；layout_elements()；add_occluder(x0, y0, x1, y1)；occlusion_sample(element_id)；clip_to_screen(element_id)；intensity(element_id)；pixel_offset(element_id)；compose_frame()；budget()；snapshot()；restore(state)。坐标统一使用归一化屏幕坐标，中心为 (0,0)，四边为 ±1，错误一律抛 LensFlareError(code, detail)。

需要修好的行为：

1. 元素链沿光源到中心方向镜像排布，offset 从 0 到 2 分别对应光源、中心、中心另一侧，元素位置为 (sx*(1-offset), sy*(1-offset))，layout_elements 先按 offset 再按 element_id 升序返回三元组；
2. offset 超出 [0,2]、weight 或 radius 非正、element_id 重复一律报 FLARE_INVALID_ELEMENT，元素数达到 max_elements 后再加报 FLARE_ELEMENT_LIMIT，两种情况都不能破坏已有元素；
3. resize 的宽高必须为正整数，否则报 FLARE_INVALID_SIZE 并保留原尺寸，size() 返回 (width, height) 二元组；
4. pixel_offset 按当前宽高把归一化位置换算成相对屏幕中心的像素偏移，以 (dx, dy) 返回，换算公式为 (nx*width/2, ny*height/2)；
5. 遮挡采样在每个元素的外接方框上铺 n×n 网格，n 取 max_samples 并夹在 1 到 64 之间，整框被遮挡物覆盖返回 0.0，没有遮挡返回 1.0，采样点取每格中心，落在任一遮挡物内部即记为被挡；
6. 强度等于 weight 乘该元素的可见采样比例，保留六位小数并夹在 0 到 weight 之间，可见比例为 0 时强度直接归零；
7. 元素方框部分越出屏幕时 clip_to_screen 返回裁剪后的矩形，完全在外返回 None，元素方框以位置为中心、半边长等于 radius，裁剪结果按 (x0, y0, x1, y1) 返回且坐标仍用归一化数值；
8. compose_frame 按强度降序、同强度按 offset 升序挑选，至多 max_flares_per_frame 个，返回含 id、box、intensity 的字典，越界元素先裁剪再参与挑选，没有候选时返回空列表；
9. budget 返回 frames、elements、samples、flares_last_frame 四个计数，frames 每调用一次 compose_frame 加一，flares_last_frame 记录该帧实际返回的条数；
10. snapshot 返回的字典必须包含 version、size、source、elements、occluders、counters 六个键，version 固定为 1，restore 遇到结构不符报 FLARE_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：set_source 与 compose_frame 并发时只能读到整份光源状态；光源落在屏幕外时元素要按各自位置单独判定，不能因为光源不可见把整条链清零；遮挡采样只命中一部分时强度按可见采样比例线性衰减，不允许全有或全无；宽高比变化后元素位置按归一化坐标重算，已缓存的像素偏移必须失效；采样点数、元素数与每帧光晕数都受上限硬顶；单帧合成只读取一次光源位置与元素表，不得在同一个元素上混用两次调用的数据。

验收：python3 -m pytest -q tests 15 条全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: lens_flare_composer
- typeName: LensFlareComposer
