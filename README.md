# batch49-py-game-b-12

## 题面

同一条广色域素材经转码服务输出到普通显示器后，暗部发灰、霓虹灯直接变白，质检把问题归到色域映射链路，要求一次性把解码、压缩、量化和抖动都理顺。同一条素材在 p3 目标下看着正常，切回 srgb 后绿色霓虹边缘发白、蓝色高光被压平，暗部还浮出一层细碎噪点，质检要求给出可复现的转换口径。改动范围限定在 gsb/batch49-py-game-b-12 的 gamut_map_chain.py，类型 GamutMapChain，公开签名保持不变：GamutMapChain(*, target="srgb", bit_depth=8, dither_strength=0.5, block_size=64, max_pixels=1000000)；set_target(target, bit_depth=None)；params()；decode(value, transfer="srgb")；encode(value, transfer="srgb")；compress_chroma(rgb)；quantize(value, *, x=0, y=0, frame=0)；convert(pixels)；out_of_gamut()；clamp_count()；stats()；snapshot()；restore(state)。错误一律抛 GamutError(code, detail)。

需要修好的行为：

1. 目标白名单只有 srgb 与 p3，其余报 GAMUT_UNKNOWN_TARGET；位深只接受 8 与 10，其余报 GAMUT_INVALID_DEPTH，失败时参数整份保持不变，params() 返回 target、bit_depth、dither_strength、block_size、max_pixels 五个键；
2. 传递函数白名单为 srgb 与 linear，未知名称报 GAMUT_UNKNOWN_TRANSFER；srgb 解码按 0.04045 分段，编码是其逆函数，linear 两个方向都原样返回，0.04045 以下走 12.92 线性段，超过阈值走 1.055 的幂函数段；
3. 构造时 block_size 超出 1 到 4096 或 max_pixels 小于 1 报 GAMUT_INVALID_BLOCK；
4. 输入像素是显示 P3 的线性光，转到 srgb 目标使用矩阵 ((1.2249, -0.2247, 0.0), (-0.0420, 1.0419, 0.0), (-0.0197, -0.0786, 1.0983))，目标为 p3 时使用单位阵；
5. 色度压缩保持亮度：先算目标空间三通道与 Rec.709 亮度 Y，任一分量越界时求最大 s∈[0,1] 使 Y+s*(c-Y) 逐通道落回 [0,1]，结果保留六位小数，亮度取目标空间三通道的 Rec.709 加权和，返回值是三元组；
6. 抖动矩阵是 4×4 Bayer，行列都先加上帧号再对 4 取模，偏移量按 (M+0.5)/16-0.5 乘 dither_strength 再除以档位数，偏移只影响量化前的通道值，不改变输出位深；
7. 量化档位为 2 的位深次方减一，四舍五入到最近档位，恰好半档取较大档，结果夹在 [0,1]，量化结果用 0 到 1 的浮点表示，不做整数打包；
8. convert 按 block_size 分块处理，逐像素解码、压缩、编码、量化，抖动坐标取 (索引 % block_size, 索引 // block_size)，超过 max_pixels 报 GAMUT_BATCH_LIMIT 且计数不变，普通输入不触发异常，计数只在成功处理后累加；
9. out_of_gamut 统计目标空间任一分量越界的像素数，clamp_count 单独统计解码前被夹紧的分量数；
10. stats 返回 converted、out_of_gamut、clamped 三个计数；snapshot 返回的字典包含 version、target、bit_depth、dither_strength、block_size、max_pixels、counters，version 固定为 1，counters 至少包含 converted、out_of_gamut、clamped 三个键，restore 遇到结构或目标名不符报 GAMUT_SNAPSHOT_INVALID 且不改动现有状态。

硬约束：完全落在目标色域内的像素必须逐通道保持原值，不允许因压缩矩阵舍入产生偏移；负亮度与超过 1.0 的高光在传递函数解码前先夹紧，夹紧计数单独统计且不影响色域越界计数；抖动序列按像素坐标与帧号确定，同一输入重复转换必须逐字节一致；参数切换整份生效，批量转换过程中不得读到半新半旧的参数；批次大小与位深受上限硬顶；同一次 convert 调用内参数不得中途变化，分块之间也不能各自读取不同参数。

验收：python3 -m pytest -q tests 15 条全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: gamut_map_chain
- typeName: GamutMapChain
