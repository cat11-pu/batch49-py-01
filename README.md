# batch49-py-game-a-12

## 题面

副本里的精英怪被控制技能命中后，巡逻进度会被整个清掉：控制结束它没有回到被打断的那一段巡逻，而是原地从头开始；连续吃两个控制时，第二个直接把第一个顶掉，恢复顺序全乱。更麻烦的是控制链结束后怪物会短暂僵直，既不巡逻也不追击，仇恨列表还留着上一个目标。战斗组要一套打断栈来管住这类 AI，涉及 gsb/batch49-py-game-a-12 下的 fsm_interrupt_stack.py，类型 FsmInterruptStack。基础状态只有一条长任务，受击、眩晕等打断以帧的形式压在栈顶，结束一层恢复一层。

公开接口：FsmInterruptStack(*, max_depth=4, default_cooldown=0)；define_state(name, *, parent=None, precondition=None)；set_base(name)；push_interrupt(name, *, source, duration, priority=0, critical=False)；pop_interrupt(*, source=None)；tick(dt)；current()；stack_depth()；progress()；set_cooldown(source, seconds)；cooldown_for(source)；history()；snapshot()；restore(snapshot)。错误统一抛 InterruptError(code, detail)。

故障与边界：
① 状态重名抛 INTERRUPT_DUPLICATE_STATE，父状态不存在抛 INTERRUPT_UNKNOWN_STATE，名字为空、source 为空、duration 非正整数抛 INTERRUPT_INVALID_REQUEST。
② set_base 只能成功一次，重复设置抛 INTERRUPT_INVALID_REQUEST；未设置基础状态时 current、push_interrupt 抛 INTERRUPT_NO_BASE；基础进度从 0 开始，空闲 tick 按 dt 累加；基础状态的父状态链只用于回退判定，不参与进度计算。
③ 每个打断帧记录状态、来源、剩余时长、优先级和 critical 标记；栈顶即 current，基础状态不计入 stack_depth，帧的来源用于冷却与误打断排查。
④ 压栈要求优先级不低于栈顶；低于栈顶直接拒绝，返回 pushed=False、reason="INTERRUPT_PRIORITY_REJECTED"，栈完全不动。
⑤ 打断期间基础状态进度冻结，tick 只递减栈顶剩余时长；剩余到 0 自动弹栈并把状态名放进返回的 expired，一个 tick 最多结算一层，多余时间不顺延到下一层，expired 按发生顺序给出本帧结束的层；tick 返回 expired、current、depth、progress 四个字段。
⑥ pop_interrupt 不指定 source 时弹栈顶；指定 source 与栈顶不符抛 INTERRUPT_SOURCE_MISMATCH 且不改栈；空栈抛 INTERRUPT_STACK_EMPTY。
⑦ 同一来源在冷却窗口内再次打断只刷新栈顶剩余时长、不新增帧，返回 refreshed=True；冷却按来源独立配置，set_cooldown 传负数或空来源抛 INTERRUPT_INVALID_REQUEST，cooldown_for 对未配置来源返回 default_cooldown。
⑧ 栈深达到 max_depth 时丢弃最底层非关键帧；critical 帧永不丢弃，全部关键时新请求抛 INTERRUPT_STACK_FULL。
⑨ 弹栈恢复下层状态时，如果该状态 precondition 返回假，则回退到它的父状态，父状态也不满足就继续上溯，不允许强行恢复。
⑩ snapshot 深拷贝基础状态、整栈剩余时长、进度、冷却与历史，快照可序列化后跨进程读取；restore 结构非法抛 INTERRUPT_SNAPSHOT_INVALID 且不变更现场，恢复后栈深与剩余时长必须逐帧一致。
⑪ 所有入口共用一把锁，push、pop、tick 原子；同一来源并发打断不得产生两个帧，history 与 stack_depth 要一致。
⑫ 每 tick 只处理栈顶一层，tick 成本与栈深无关；1000 条历史下不得每帧重扫全部帧。

验收：python3 -m pytest -q tests 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启恢复自行验证；只做本地实现，错误码与返回字段必须与上述名称一致。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: fsm_interrupt_stack
- typeName: FsmInterruptStack
