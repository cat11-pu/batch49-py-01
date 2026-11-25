# batch49-py-safety-a-15

## 题面

批处理节点的临时目录最近出了两次事故：上一代作业已经跑完，清理脚本却把正在写的新一代目录一起删了，第二天回放全失败；作业还在用某个目录，清理程序不看租约直接删，进程写了一半；删除失败（只读挂载、权限不足）脚本直接跳过，脏目录越堆越多，没人知道哪些失败过；目录被别的作业 rename 到另一代根下，清理按路径判断归属，删错了对象；清理器删到一半被 kill，重启后从第一层重来，子目录里的文件成了孤儿；磁盘告警时统计只有一句删了多少，看不出跳过、隔离、失败各多少。这次收进 gsb/batch49-py-safety-a-15 的 temp_generation_sweeper.py，类 TempGenerationSweeper 管代次、租约、冻结、自底向上删除与隔离重试，异常统一为 SweepError(code, detail)。目录台账为内存模型，每代根为 <root>/g<代次>，inode 自 1 递增。

公开接口：TempGenerationSweeper(*, root, max_dirs_per_gen=64, max_bytes=1<<20, quarantine_limit=8, clock=None)；begin_generation(*, label=None, now=0)；current_generation()；freeze(gen)；create(path, *, bytes=0, inode=None)；acquire(path, *, lease)；release(path, *, lease)；rename(path, new_path)；sweep(*, gen=None, now=0)；dirs(*, gen=None)；quarantine_report()；retry_quarantine(*, now=0)；inject_fault(path, *, code="EACCES", times=1)；snapshot()；restore(state)；stats()。

要求逐条落地：①begin_generation 建新代并把上一代冻结，返回代次对象（含 gen、label、state、created_at），current_generation 返回活跃代号（没有代次时为 0），freeze 可显式冻结指定代且对已冻结代幂等；②create 只能落在活跃代根 <root>/g<代次> 之下，越界报 PATH_OUTSIDE，bytes 为负报 INVALID_REQUEST，重复路径报 DIR_EXISTS，每代目录数超 max_dirs_per_gen 报 DIR_LIMIT，每代字节数超 max_bytes 报 BYTE_LIMIT，返回的条目含 path、gen、inode、bytes；③acquire 只能作用于已登记路径否则报 PATH_UNKNOWN，同路径同租约重复登记报 LEASE_EXISTS，释放不存在的租约报 LEASE_UNKNOWN，有活动租约的目录不得删除；④sweep 只处理 frozen 代次，指定活跃代报 GEN_ACTIVE，未知代报 GEN_UNKNOWN，未指定时按代次升序处理全部 frozen 代，返回 deleted、skipped、quarantined、failed 四个元组的字典，dirs 按路径升序返回；⑤删除自底向上，同一批内层级由深到浅、同层路径升序，父目录还有未删子项时必须跳过；⑥有活动租约的条目计入 skipped 并保留，租约释放后的下一次 sweep 才删除，租约释放与 sweep 并发时同一条目只能被观察到一次删除；⑦同一代次重复 sweep 幂等，已删条目计入 skipped（按路径升序）且不再计入 deleted；⑧删除失败的目录移入隔离区并从代次移除，quarantine_report 返回 path、gen、inode、code、attempts，隔离区超过 quarantine_limit 时报 QUARANTINE_LIMIT 并把该条计入 failed，条目保留；⑨retry_quarantine 重试隔离区，故障已清除的删除并计入 deleted，未清除的 attempts 加一并保留，返回 deleted 与 retained 两个计数；⑩stats 按 deleted、skipped、quarantined、failed 四类逐条对账，另单列 delayed，表示已删但仍被外部进程打开、空间延迟释放的条目；⑪目录被 rename 到另一代次根下时，归属按 inode 判定而不是路径，清理只作用于它原本所属的代次；⑫清理器在删除一半时崩溃，重启后剩余子项仍要按深度续删，不得从头重来或漏删。

硬约束：冻结与 create 互斥，sweep 只作用于冻结代次；代次状态、隔离区与计数要落盘（snapshot/restore），重启后 frozen 代次仍可清理；目录数与字节数都是硬顶。

验收：`python3 -m pytest -q tests` 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: temp_generation_sweeper
- typeName: TempGenerationSweeper
