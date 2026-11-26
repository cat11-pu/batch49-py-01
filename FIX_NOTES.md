# 修复说明

`cgroup_freeze_coordinator.py` 已从占位实现补全为完整协调器。

## 核心修复

- 四态状态机：`thawed -> freezing -> frozen -> thawing -> thawed`；重复冻结保持幂等，`thawing` 期间冻结返回 `FREEZE_BUSY`。
- 进程检查：退出进程拒绝冻结，僵尸仅累加警告，fork 按 `retry_limit` 重试并累计次数；拒绝路径不改变状态、配额和用量。
- 用量与速率：采样只允许在 `thawed` 且单调递增；冻结期沿用冻结时快照；`frozen_ms` 在最终解冻时结算并包含 `thawing` 段；rate 窗口扣除冻结时长，采样切换不会重复计时。
- 批量原子性：批量冻结/解冻按输入顺序执行，失败时按成功顺序回滚；预先冻结的组不回滚，回滚恢复原状态且修正事件计数、冻结时长清零。
- 硬顶与异常：批量条数、单组进程数先校验再变更；统一保留 `FreezeError(code, detail[path])`。
- 日志与恢复：每次状态变更前写 `path/phase/ts` 意图；`recover` 将未完成的 `freezing/thawing` 收敛到 `thawed`。
- 并发与层级：全局 RLock 保证同组并发冻结只产生一次事件；批处理逐组提交，status 可观察各组各自状态；父子组状态互不级联；已删除组不会被 thaw/恢复重建。

## 验证记录

```text
$ PYTHONPATH=/home/cat/gsb2/uid4829/.venv/lib/python3.14/site-packages \
    python3 -m pytest -q tests
...............                                                          [100%]
15 passed in 0.01s

$ /home/cat/gsb/uid5593/.venv/bin/python -m pytest -q tests
...............                                                          [100%]
15 passed in 0.02s
```

额外本地自检通过：200 轮 16 线程同组并发冻结（每轮仅 1 次 `freeze_events`）、批量执行期间逐组状态、三组顺序回滚、父子/删除语义、用量与 rate 冻结窗口、日志先于状态、重启收敛。
