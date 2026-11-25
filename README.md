# batch49-py-game-c-04

## 题面

换播控单元后解码器先花屏再绿屏：编码器把同一 ID 的 SPS 换了内容，没到关键帧后续帧就按新参数解；从快照恢复后帧引用的版本号比当前小，整片错位。故障持续了半场球赛，机位切换时画面撕裂，慢动作回放整段作废；导播台重启后缓存从旧快照恢复，已经激活的参数集版本回退，后续 P 帧全花，只能等下个关键帧才恢复；机房的备份解码器同时吃到了新版本，两台设备输出的画面颜色都不一致，比对录像时无法判断哪一版才是正确基准，播出安全等级被降了一档。范围是 gsb/batch49-py-game-c-04 的 parameter_set_cache.py，类 ParameterSetCache，按类型与 ID 缓存参数集，管版本留存、按路激活和关键帧绑定。

公开接口：ParameterSetCache(max_entries_per_kind, max_payload_bytes, max_versions)、put(kind, ps_id, payload)、get(kind, ps_id, version)、activate(stream_id, kind, ps_id)、bind_frame(stream_id, keyframe)、invalidate、generation、entries、snapshot、restore、stats；拒绝抛 ParameterSetError(code, detail)，错误码与 stats 键取模块顶部 ERROR_CODES、STATS_KEYS。

边界：①类型只认 sps/pps/vps，ID 上限 31/255/15，布尔与字符串 ID 拒；②payload 须非空字节串，超 max_payload_bytes 拒，被拒的写入不得覆盖已有有效条目；③同 ID 同内容再写幂等，版本不变；内容变了版本加一，只留最近 max_versions 版；④每种条目超 max_entries_per_kind 时新 ID 拒，覆盖已有仍允许；⑤get 不给版本取最新，给了取那条，超期报版本缺失；invalidate 删整个 ID 并返回是否删到；⑥首次 activate 即生效；同路同类型已有生效版本时挂起，bind_frame 只在关键帧应用并加代次；⑦同 ID 内容变化未到关键帧时旧帧用旧版本，新版本只给之后关键帧；⑧ID 越界或 payload 为空必须拒，且不得覆盖同 ID 已有的有效条目；⑨快照恢复后版本号继续单调，不得回退到快照前旧版本；⑩entries 按 ID 升序报 ps_id、version、size，stats 按键计数。

补充口径：put 的 version 从 1 开始，changed 只在内容变化时为真，同 ID 同内容重复写入不算变更，但仍要计入 puts；版本链只保留最近 max_versions 个，被淘汰的版本查询按版本缺失报错，不能拿最近版本顶替；entries 的 size 是 payload 字节数；bind_frame 返回的 active 是深拷贝，调用方改它不得影响下一次查询；generation 只在关键帧真正应用挂起版本时加一，普通帧与无待应用状态都不加。

硬约束：写入与查询并发时同一条参数集只能看到旧版或新版，不得读到半条；挂起版本与生效版本分离，同一路同一类型未到关键帧不得提前应用，也不得把挂起状态丢掉；每种条目数与单条 payload 长度都是硬上限，超限拒绝且不覆盖同 ID 的有效条目。snapshot 脱离活对象，restore 后条目、版本内容、生效与挂起都在，非法快照拒。

验收：`python3 -m pytest -q tests` 15 条全过；不改测试、不跳过、不写死；并发与重启自验；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: parameter_set_cache
- typeName: ParameterSetCache
