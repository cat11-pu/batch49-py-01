# batch49-py-game-c-01

## 题面

直播返送台这周连着出岔子。主持人话筒推到正中时，监听电平反而比偏在一侧低一截，导播以为话筒坏了，换了两支才发现是声像分配的问题，事后翻回放，同一段人声在左右声道上的能量忽高忽低；主持人说话中途推声像，耳机里蹦出一下爆响，嘉宾当场摘了耳机；音频师把声像律从等功率改回线性，电平表当场掉一格，监听席隔着玻璃都听得出来。

范围是 gsb/batch49-py-game-c-01 的 pan_law_mixer.py，类 PanLawMixer，把若干单声道源按声像位置分配到一条立体声总线上，并按采样推进声像与增益的斜坡。它不管编解码与重采样，只负责让送到总线的每一对左右采样值可预测、可复现。公开接口：PanLawMixer(max_sources, max_ramp_samples)、set_pan_law(law, fade_samples)、add_source(source_id, pan, gain)、remove_source(source_id, immediate)、set_pan(source_id, pan, ramp_samples)、set_gain(source_id, gain, ramp_samples)、ramp_remaining(source_id)、render(frames)、meter(source_id)、snapshot()、restore(state)、stats()；拒绝抛 PanLawError(code, detail)，错误码与 stats 键取模块顶部 ERROR_CODES、STATS_KEYS。

边界：
①空标识、非串、pan 越界、负 gain 拒，任何一条被拒都不许改动已有源；
②重复加入或超 max_sources 拒，被拒的那一次不能占掉名额；
③声像律只认 linear、equal_power、minus_6db，其他名字当场拒，也不许把旧律悄悄留着；
④线性律 -1/1/0.5 出 (1,0)、(0,1)、(0.25,0.75)，写的是左右系数而不是电压；
⑤等功率律 theta=(pan+1)*pi/4，居中左右约 0.707 而非 0.5，硬左硬右仍是 1.0 与 0.0，左右平方和为 1；
⑥minus_6db 在等功率结果上再乘 2^-0.5，居中约 0.5，硬右约 0.7071，用于对齐老素材的推子刻度；
⑦ramp_samples 是采样数，第 i 个采样走 i/N，第 N 个采样恰达目标，中途不提前到也不超调；
⑧斜坡未走完时再改声像，从当前插值位置续接到新目标，不回零也不跳回旧目标，听感上不能出现二次爆音；
⑨切律下一采样即按新律重算当前声像，带 fade_samples 时在 N 个采样内从旧律系数过渡到新律系数，过渡期间不得出现直流跳变；
⑩移除带斜坡的源按剩余采样数淡出，斜坡为零才允许 immediate 移除，否则要拒，别在总线上留下断掉的尾音；
⑪sources 字典序，stats 按键计数，render 恰好 frames 个 (left,right)，frames=0 返回空表，负数与非整数拒，meter 报左右系数、gain 与 ramp_remaining。

硬约束：加源、改参数与渲染可能同时发生，锁要覆盖整次采样块的取数与推进，任何一块要么完整看到某个源要么完全看不到，计数不重不漏；源数与单次斜坡长度都有硬上限，超限必须拒而不是排队，更不许把已接入的源挤掉；snapshot 要脱离活对象，restore 后律、声像、增益、斜坡进度与计数都要回来，结构不合法的快照整份拒掉且不触动当前状态。

验收：`python3 -m pytest -q tests` 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: pan_law_mixer
- typeName: PanLawMixer
