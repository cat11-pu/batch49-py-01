# batch49-py-game-c-03

## 题面

连线头一天，主播端按 4K 推给只标了 1080p 的会议室，画面全黑；同一档 profile 换成低 level 反而通了；第二天有人把打包格式从 annexb 改成 avcc，协商还沿用上一轮结果，直到重启进程才变。这类事故在跨端开会时最难查：接收端的能力表是运营手填的，发送端按自己最高档往前顶，两边都以为对方能接，最后黑屏十几秒才退回低档，观众只看到一片马赛克和一行「连接中」；运维只能靠重启进程冲掉缓存，已建立的几路会话跟着全断。

范围是 gsb/batch49-py-game-c-03 的 codec_capability_matcher.py，类 CodecCapabilityMatcher，在发送端与接收端之间按 codec、profile、level、分辨率、帧率与打包格式求交集，匹配不到时按优先级回退到下一个候选，并把协商结果按会话缓存。它只做能力判定，不搬运码流，也不管重连。公开接口：CodecCapabilityMatcher(max_caps_per_endpoint, max_chain, max_sessions)、register_endpoint(endpoint_id, caps)、match(sender, receiver)、fallback_chain(sender, receiver)、negotiate(session_id, sender, receiver)、session_result、mark_keyframe、explain、snapshot、restore、stats；拒绝抛 CodecError(code, detail)，错误码、reason 与 stats 键取模块顶部 ERROR_CODES、REASON_CODES、STATS_KEYS。

边界：
①能力项七键缺一、未知 codec 或 profile 或 level、非正分辨率、未知打包格式都拒，被拒的注册不落盘；
②能力超 max_caps_per_endpoint 或标识为空拒，端点表保持上一次可用的一套；
③level 40/41/51/52 各管 1080p30、1080p60、2160p30、2160p60，所需 level 按发送端的实际分辨率与帧率算；
④接收端 level 盖不住所需 level，或宽高帧率不够，就拒并给 LEVEL_TOO_LOW；
⑤codec、打包、profile 对不上分别给不同的 reason 码，别混成一个笼统的失败；
⑥fallback_chain 先按 profile 从高到低再按 level 从高到低，不跳中间档，每项带 usable；
⑦接收端声明 level 低但实际参数落在该档范围内时按实际参数放行，不按声明值一刀切；
⑧negotiate 取第一个可用候选并按会话缓存，能力没变的重复调用标 cached；
⑨端点重新注册后缓存作废并重算，正在传输的会话沿用旧结果到下一个关键帧，mark_keyframe 没有待生效重算时不改代次，关键帧之前换参数会让解码器引用错的表，宁可晚一帧生效也不要花屏；
⑩explain 报 required_level 与理由，stats 按键计数；
⑪同一会话重复协商要幂等，不能因为调用次数多就换候选或把代次推高。

硬约束：注册与协商可能并发，读到的端点能力要么是整套旧的要么是整套新的，不得半新半旧；会话表有上限，超限要拒而不是悄悄挤掉老会话；snapshot 脱离活对象，restore 后端点能力、会话结果、缓存与计数都在，非法快照整份拒且不影响在场会话，上限判定在锁内一次完成。

验收：`python3 -m pytest -q tests` 15 条全过；不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: codec_capability_matcher
- typeName: CodecCapabilityMatcher
