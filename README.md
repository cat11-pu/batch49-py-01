# batch49-py-game-a-30

## 题面

比赛回放上传后，运营在后台改了一个字段准备重发，结果整份回放被判成损坏；另一场比赛的回放是旧版本客户端录的，里面几种事件现在完全不认识，加载直接失败；还有一场回放只有前半段可信，导出时却整份被拒，连能用的部分都拿不到。版本发布当晚，回放服务连着报了三条不同原因的错误，值班同学只能一份份手工重传。范围是 gsb/batch49-py-game-a-30 下的 replay_integrity_guard.py，类 ReplayIntegrityGuard，负责分段摘要链、篡改定位与跨版本事件映射。

公开接口：ReplayIntegrityGuard(*, max_segments=256)；seal_segment(index, events, *, version=1)；patch_events(index, events)；verify()；locate_tamper()；map_event(raw, *, from_version)；export_prefix(max_segments=None)；segments()；snapshot()；restore(state)；stats()。摘要算法为 SHA-256，对按规范化 JSON（键排序、无多余空格）序列化后的 index、version、prev_digest、events 求十六进制摘要，首段 prev_digest 为 64 个 0。错误抛 ReplayGuardError(code, detail)：REPLAY_INVALID_REQUEST、REPLAY_SEGMENT_ORDER、REPLAY_SEGMENT_LIMIT、REPLAY_UNKNOWN_SEGMENT、REPLAY_STATE_INVALID。SegmentDigest 含 index、version、event_count、digest、prev_digest；stats 有 sealed、patched、verified_ok、verified_bad、exported 五个计数，构造时全部为零。

需要处理的情况：① 段序号必须从 0 连续封存，跳号或重复封存抛 REPLAY_SEGMENT_ORDER；② 每段摘要链式包含前段摘要，段顺序不可交换；③ 空事件列表、非字典事件、非 JSON 可序列化内容或版本号非正数拒绝封存，失败时不得留下半条链；④ verify 返回布尔值，成功计入 verified_ok，失败计入 verified_bad；⑤ locate_tamper 返回首个不可信段的序号，全部通过返回 None；⑥ patch_events 只替换段内容、不重算摘要，改错的位置交由 verify 发现，未知段抛 REPLAY_UNKNOWN_SEGMENT；⑦ 中间段被替换后，后续所有段都要标记不可信，不能只认坏掉那一段；⑧ 旧版本映射：1 版把 hp 改名为 health、ts 改名为 tick、src 改名为 source，2 版把 hp 改名为 health、amt 改名为 value，3 版及以上原样保留，映射结果统一附上 schema_version 为 3、其余字段原样带过，两版表都命中的字段以对应版本的表为准；⑨ 旧版本中无法识别的未知事件类型保留为不透明记录，不得让整份回放不可加载；⑩ export_prefix 按段顺序摊平事件，每条附带 segment 与从 1 起算的全局 seq，参数为最大段序号且包含该段，摊平不改变事件内容，校验失败时仍可导出已通过校验的前缀段；⑪ 快照深拷贝，恢复时结构不合法抛 REPLAY_STATE_INVALID 且现有状态不动。

硬约束：段封存与查询并发时链关系不得错乱，序号与摘要必须成对写入，封存只追加不覆盖、已有段只能经 patch_events 改内容；快照恢复后段内容、摘要与统计都要还原且与快照不共享引用；段数上限 max_segments 为硬上限。

验收：`python3 -m pytest -q tests` 共 15 条必须全过，不得改测试、跳过或按固定输入写死；并发封存、跨版本映射与重启恢复都要自己构造场景验证；只做本地实现，不引入第三方依赖。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: replay_integrity_guard
- typeName: ReplayIntegrityGuard
