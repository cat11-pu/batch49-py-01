# batch49-py-game-c-07

## 题面

点播平台按分段换密钥，某部剧第七集开始观众频繁解密失败，回看旧集却正常，运维发现轮换点落在分段中间，宽限期也算错了，相邻两集切换密钥的时点不一致，客户端在边界上会同时拿到两个代号。处理范围是 gsb/batch49-py-game-c-07 的 segment_key_rotation_planner.py，类 SegmentKeyRotationPlanner 负责分段周期、密钥轮换、宽限窗口与许可证签发。分段编号从 0 开始，每个分段只登记一次，周期与宽限都以分段为单位计数。

公开接口：SegmentKeyRotationPlanner(period_ms=6000, grace_segments=1, max_keys=8, max_revocations=64)；add_segment(segment_index, duration_ms) -> int；epoch_for_segment(segment_index) -> int；register_key(key_id, epoch) -> None；key_count() -> int；rotate(segment_index, key_id) -> str | None；grace_window(at_segment=None) -> tuple[str,str] | None；active_key() -> str | None；revoke(key_id) -> None；is_revoked(key_id) -> bool；license_for(segment_index, ttl_segments=3) -> str；license_usable(license_id, at_segment) -> bool；stats() -> dict；snapshot() -> dict；restore(state) -> None。错误抛 KeyRotationError，code 用 SEGMENT_INVALID、SEGMENT_OUT_OF_ORDER、SEGMENT_UNKNOWN、KEY_INVALID、KEY_DUPLICATE、KEY_UNKNOWN、KEY_LIMIT、ROTATION_NOT_AT_BOUNDARY、ROTATION_CONFLICT、KEY_EPOCH_MISMATCH、REVOCATION_LIMIT、LICENSE_UNKNOWN、SNAPSHOT_INVALID。

故障与边界：①分段必须按 0、1、2 顺序追加，时长非正整数或索引跳号直接拒绝；②每个分段的代号按起始时刻落在哪个周期决定，跨周期的分段归属起始时刻所在周期；③同一代号重复注册报冲突，密钥总数封顶，超限报错而不是顶掉旧密钥；④轮换只能发生在周期起点，分段起点不在周期边界时拒绝；⑤待启用代号必须登记在目标分段所在周期，否则拒绝；⑥同一分段已经有别的代号生效时再换一把视为冲突，同一把重复轮换按幂等处理只记一次；⑦宽限窗口按分段数计，窗口结束后旧代号自动进吊销表，宽限窗口内吊销旧代号只影响新许可申请，已签发且未到期的许可继续可用；⑧吊销表条目封顶，超限报错而不是覆盖旧条目；⑨许可证按分段所属周期的代号签发，记录到期分段，到了到期分段不可用；⑩查询未登记分段、未登记许可证或未知代号一律报错；⑪分段时长不规则时要按累计时长判断轮换点，不能拿分段序号乘周期硬算；⑫代号回绕时不得与仍在宽限期的旧代号重号，冲突要报错而不是复用。

硬约束：签发许可证与轮换、吊销并发时，同一分段最终只能有一个代号，不得出现两把密钥同时被判有效；快照要带走分段表、代号、宽限、吊销、许可证与计数，restore 校验失败抛 SNAPSHOT_INVALID 且现有状态不变，恢复后 grace_window 与 license_usable 的结论要和快照时一致。轮换、宽限到期与吊销都按分段推进，不依赖真实时钟，回放同一串分段应得到同样的代号序列。stats 至少给出 segments、keys、rotations、revocations、licenses 五个计数。

验收：python3 -m pytest -q tests 15 条全过，不改测试、不跳过、不按样例写死；并发轮换与重启恢复自行验证；错误码以外的异常一律不许吞掉；分段表与许可证都要能按序回放；只在本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: segment_key_rotation_planner
- typeName: SegmentKeyRotationPlanner
