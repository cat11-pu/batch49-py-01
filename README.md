# batch49-py-game-a-29

## 题面

一场四十人团本打完，策划要复盘最后三分钟，导出的战斗日志却缺了开头，中间还夹着重复的伤害行；客服想找死亡事件，翻到一半发现那段已经被后来的日志挤掉；导出一个跨越旧区间的请求还会因为一条缺失序号直接整段报错。运维还发现同一场战斗被重复上报了两次，事后只能人工对齐。范围是 gsb/batch49-py-game-a-29 下的 battle_log_retainer.py，类 BattleLogRetainer，负责有界日志缓冲、关键事件钉扎、采样降频与区间导出。

公开接口：BattleLogRetainer(*, capacity=64, sample_rate=1.0)；append(kind, payload=None, *, level=0, now=None)；pin(seq)；unpin(seq)；set_sample_rate(rate)；export_range(start_seq, end_seq)；entries()；size()；snapshot()；restore(state)；stats()。序号从 1 连续递增，只发给真正写入缓冲的事件。错误抛 BattleLogError(code, detail)：BATTLE_LOG_INVALID_REQUEST、BATTLE_LOG_UNKNOWN_SEQ、BATTLE_LOG_STATE_INVALID。LogEntry 含 seq、kind、level、payload、pinned；stats 有 appended、sampled_out、evicted、exported、unpinned 五个计数，构造时全部为零。

需要处理的情况：① append 校验 kind 为非空字符串、level 为非负整数，否则拒绝且不消耗序号；② 容量满时优先淘汰未钉扎的最旧事件，evicted 累计；③ 采样率范围为 (0, 1]，周期为 1 除以采样率四舍五入取整、最小为 1，按到达次序每满一个周期保留一条事件，不按时间窗裁剪，被丢弃的计入 sampled_out 且不占序号，改采样率时到达计数不重置；④ 钉扎事件永远逐条保留，不受采样影响；⑤ pin 不存在的序号抛 BATTLE_LOG_UNKNOWN_SEQ，重复钉扎保持幂等；⑥ unpin 返回是否真的解钉，解钉后的事件重新可被淘汰，成功解钉计入 unpinned；⑦ export_range 两端闭区间，按原始顺序返回，记录含 seq、kind、level、payload、pinned，exported 累计条数；⑧ 导出起始序号小于 1、非整数或起止倒置时拒绝，序号必须按正整数处理；⑨ 钉扎事件超出容量上限时按等级淘汰最低等级钉扎项，而不是拒绝新的钉扎；⑩ 导出区间跨越已被淘汰或从未写入的序号时，连续缺失合并为一条 gap 记录而不是逐条报错；⑪ entries 与 snapshot 都返回深拷贝，恢复时结构不合法抛 BATTLE_LOG_STATE_INVALID 且现有状态不动；⑫ append 返回写入后的完整记录，被采样丢弃时返回 None，两种结果都不影响后续序号，返回内容要与 entries 中读到的保持一致。

硬约束：并发写入不得出现重复序号或丢条，size 与统计必须同步；快照恢复后序号、钉扎状态、采样到达计数与统计都要还原且与快照不共享引用；容量为硬上限，容量检查与统计更新在同一临界区完成，导出始终按序号升序返回，不因淘汰改写历史顺序。

验收：`python3 -m pytest -q tests` 共 15 条必须全过，不得改测试、跳过或按固定输入写死；并发写入、采样节拍与重启恢复都要自己构造场景验证；只做本地实现，不引入第三方依赖。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: battle_log_retainer
- typeName: BattleLogRetainer
