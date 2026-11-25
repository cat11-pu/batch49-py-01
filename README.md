# batch49-py-safety-b-10

## 题面

补丁窗口只剩四十分钟，运维把一批资产按业务组排成三波，第一波上线后健康检查报红，值班同学急着回滚，结果把同组另一批手工热修也一起撤了；第二次重试时补偿脚本对已经撤销过的资产又跑了一遍，回滚日志里出现重复条目；更糟的是有一波健康检查超时，脚本当成通过直接放行了下一波，故障面扩大。事后复盘发现，问题不在补丁本身，而在波次之间没有门槛、回滚范围没有边界。请在 gsb/batch49-py-safety-b-10/patch_wave_orchestrator.py 中实现 PatchWaveOrchestrator，把补丁按资产分组排成波次，每波完成后检查健康指标再放行，失败只回滚本波及后续未完成部分。

构造函数为 PatchWaveOrchestrator(apply, revert, *, max_waves=8)，apply 与 revert 是资产级回调。公开接口：plan(assets) -> list、run_next(health_for) -> WaveResult、run_all(health_for) -> list、pause() -> None、resume() -> None、status() -> dict、rollback(wave_index=None) -> tuple、replay(wave_index) -> WaveResult、applied_order() -> list、pending() -> list、snapshot() -> dict、restore(state) -> None。模型为 Asset（asset_id、group、patch）、WaveState（index、group、status、applied、attempts）、WaveResult（index、status、applied、health），PatchWaveError 带 code 与 detail。

故障与边界：
①plan 按 group 首次出现次序排波，波内资产按 asset_id 升序，初始状态 pending、applied 为空，同一资产只能出现在一个波次，波次状态只前进不回退，除非显式回滚或重放，pending 按波序与波内次序列出尚未完成的资产。
②asset_id 重复抛 ASSET_DUPLICATE；波数超过 max_waves 抛 WAVE_LIMIT_EXCEEDED，两种情况都不得留下半成品计划，失败后重新 plan 要从零开始。
③run_next 对当前 pending 波逐个调用 apply，再调用 health_for(波序号) 取带 ok 的映射，ok 为真记 applied，否则记 failed 并返回；apply 抛错按该波失败处理，已应用的资产留在记录里。
④存在 failed 波时 run_next 抛 WAVE_BLOCKED，必须先 replay 或回滚，不能自动放行下一波；健康检查超时按未通过处理并停在当前波次。
⑤暂停期间 run_next 抛 ORCHESTRATOR_PAUSED，resume 后恢复，暂停只影响新波次，不打断正在执行的 apply。
⑥rollback 传入波序号只撤销该波已应用资产，按应用逆序调用 revert，波标记 rolled_back 并返回被撤销的资产序列，返回值就是 revert 的调用顺序；不传参数时针对最后一个 applied 或 failed 的波。
⑦回滚不得把波次之外的手工变更一起撤掉，只撤销本批次写入的部分；已经通过的波次保持不动。
⑧同一波次重复回滚不得重复执行已成功的补偿步骤。
⑨replay 只补该波尚未应用的资产，重复调用不得重复 apply，随后重新做健康检查，重放后再失败仍停在原波次。
⑩run_all 依次调用 run_next 直到没有 pending 波或出现失败，返回结果按波序号排列，每条都要带健康结果。
⑪status 返回 paused、total、counts 与 applied_order，counts 恒含 pending、applied、failed、rolled_back 四个键，total 为波总数。
⑫snapshot 返回拍摄时刻的独立副本并包含暂停标记，restore 遇结构不合法状态抛 SNAPSHOT_INVALID 且不改动现状，恢复后可继续跑下一波。

验收：python3 -m pytest -q tests 15 条必须全部通过；不得修改测试、不得跳过、不得按固定输入写死；每波健康门槛与回滚顺序自行验证；只做本地实现，不联网，不引第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: patch_wave_orchestrator
- typeName: PatchWaveOrchestrator
- file: patch_wave_orchestrator.py
