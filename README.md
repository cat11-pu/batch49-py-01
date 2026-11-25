# batch49-py-game-c-15

## 题面

现场多机位同步录制，回放时两台机器的时码差了两帧，导播台切换素材会跳一下；排查发现从时钟在抖动大的链路里频繁大跳，越调越远，主时钟短暂失联后本地时钟又按旧偏移一直漂。还有一路延迟请求丢了响应，伺服却拿过期链路延迟继续驯频率，把偏差放大。范围落在 gsb/batch49-py-game-c-15 的 ptp_slave_servo.py，类型 PtpSlaveServo，负责同步与延迟报文采样、偏移估计、步进与频率驯服、失联保持与快照。

公开接口：PtpSlaveServo(*, window=8, step_threshold_ns=1000000, max_freq_ppb=1000, slew_divisor=100, max_steps=4, holdover_limit_ns=10000000, delay_age=5)；feed_sync(seq, master_send_ns, slave_recv_ns)；feed_delay_req(seq, request_send_ns, response_recv_ns)；offset_estimate()；servo_update()；holdover(elapsed_ns)；step_threshold()；clock_ns()；frequency_ppb()；link_delay()；window_size()；snapshot()、restore(state)；stats()。stats 返回 sync_samples、dropped、delay_updates、steps、servo_updates、restores 六项。ServoResult 含 offset_ns、mode、freq_ppb、step_count，mode 取 step 或 slew，HoldoverResult 含 offset_ns、available、elapsed_ns；拒绝一律抛 PtpError(code, detail)。

要查清的现象与边界：① 延迟请求按单向时延取往返差的一半，响应早于请求报 BAD_DELAY，序号不大于已处理值的报文丢弃且不改链路延迟，首个有效延迟请求到达前读取链路延迟报 NO_DELAY；② 同步报文必须已有有效链路延迟才能参与估计，否则报 NO_DELAY；偏移等于本地接收减主时钟发送再减链路延迟；③ 样本按序号严格递增接收，被接受的样本返回真、被丢弃的返回假，重复与回退的序号计入 dropped，不进入窗口；④ 窗口只保留最近 window 个样本，奇数个取中位数，偶数个取中间两个的整数平均；⑤ 没有样本时估计偏移或更新伺服都报 NO_SAMPLES；⑥ 偏移绝对值严格大于 step_threshold_ns 才步进，等于阈值仍走驯服；步进把偏移加到本地时钟并清空窗口，步进次数加一；⑦ 步进次数超过 max_steps 后报 STEP_LIMIT，本次不改变本地时钟与步进计数；⑧ 未过阈值时按偏移除以 slew_divisor 得到频率修正，并夹在正负 max_freq_ppb 之间，方向随偏移符号；⑨ 保持模式按最后一次偏移与当前频率外推，外推误差超过 holdover_limit_ns 时结果标记不可用；⑩ 连续样本偏移同号且超过步进阈值时只步进一次，步进后窗口清空重新累积，避免连续大步；⑪ 保持模式期间累计外推误差超过上限时输出不可用标记，恢复后先步进再驯服；⑫ 延迟请求响应缺失时用最近有效链路延迟，超过老化时间则只更新偏移不更新频率；多线程喂样本与读取时钟必须加锁，同一序号并发到达只能进入窗口一次；快照是深拷贝，恢复后窗口、链路延迟、频率、步进次数与保持状态都在，结构不全报 SNAPSHOT_INVALID 且原状态不动。

硬约束：样本窗口、最大频偏与步进次数都是硬上限；任何一次被拒的报文不得改变窗口或频率；同一序号并发到达只能生效一次。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与保持恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: ptp_slave_servo
- typeName: PtpSlaveServo
