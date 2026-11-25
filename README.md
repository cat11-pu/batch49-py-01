# batch49-py-game-a-11

## 题面

排位赛的怪物在换阶段时偶尔会卡在半只脚踏进新阶段的姿势：该退出的旧阶段没有清理，该进入的新阶段回调也没走完，日志里父阶段和子阶段来回错位。问题在重连和批量刷怪时更明显，半个状态的怪物既不掉落也不攻击，玩家反馈很难复现。战斗组把这块交给层级状态机重做，先从最小可复现的 windup 到 swing 链路查起，涉及 gsb/batch49-py-game-a-11 下的 hierarchical_fsm.py，类型 HierarchicalStateMachine。它维护一棵以 root 为根的父子状态树，外部只提交转换请求，由每帧一次的 tick 统一处理。

公开接口：HierarchicalStateMachine(*, root="root", max_pending=32)；define_state(name, *, parent=None, priority=0, guard=None)；set_initial(name)；request_transition(target, *, priority=0, reason="")；tick()；current_path()；current_state()；register_hooks(state, *, on_enter=None, on_exit=None)；pending_count()；history()；stats()；snapshot()；restore(snapshot)。不合法操作统一抛 FsmError(code, detail)。

故障与边界：
① 构造时自动建立根状态，根不能重定义也不会被退出；状态重名抛 FSM_DUPLICATE_STATE，父状态不存在抛 FSM_UNKNOWN_STATE，名字为空、负优先级或 guard 不可调用抛 FSM_INVALID_REQUEST。
② set_initial 从根的子节点到目标逐层激活，进入回调按父到子顺序触发；未设置初始状态就提交请求或 tick 抛 FSM_NO_INITIAL，重复调用 set_initial 一律抛 FSM_INVALID_REQUEST。
③ 转到后代只进入缺失的子链，不退出公共祖先，公共祖先只计算一次；转到兄弟先按子到父退出到公共祖先，再按父到子进入目标链；转到祖先按子到父退出，目标自身不重复触发进入。
④ 守卫挂在目标状态上、无参调用，返回假值时保持原路径，tick 返回 executed=False、rejected="guard_denied"，请求原 reason 原样带回，stats 的 rejected 加一。
⑤ tick 每帧最多执行一条转换，按优先级从高到低挑选，同优先级按请求先后；队列按请求到达顺序编号，未被选中的请求当场作废并计入 discarded；tick 返回 executed、from、to、reason、rejected、discarded 六个字段。
⑥ 被选中的请求如果被守卫拒绝，本帧其余请求同样作废，不允许降级执行下一条。
⑦ 待处理队列上限 max_pending，满了再提交抛 FSM_QUEUE_FULL，已入队请求不受影响，被拒绝的请求不占用队列名额。
⑧ 退出回调抛异常时，剩余退出仍要走完并最终停在根状态，hook_errors 加一，已入队请求清空，不允许停在半退出路径。
⑨ register_hooks 对同一状态再次调用会整体覆盖两条回调；未定义状态抛 FSM_UNKNOWN_STATE。
⑩ snapshot 深拷贝路径、历史、计数和待处理请求，快照可跨进程重启后恢复；restore 遇到结构非法抛 FSM_SNAPSHOT_INVALID 且不改动现场，恢复成功后待处理队列必须为空。
⑪ 所有修改入口用同一把可重入锁保护，request、tick、snapshot 之间不得交错出半更新路径；history 只追加，stats 只读。
⑫ 单帧处理时间随状态深度线性增长，深链 64 层、每帧 500 条请求的基准下不得出现平方级扫描，历史与统计查询保持在毫秒级。

验收：python3 -m pytest -q tests 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与快照重启自行验证；只做本地实现，所有错误码与返回字段必须与上述名称一致。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: hierarchical_fsm
- typeName: HierarchicalStateMachine
