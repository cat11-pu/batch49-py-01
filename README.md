# batch49-py-safety-b-05

## 平台字段（非题面）

| 字段 | 值 |
| --- | --- |
| name | batch49-py-safety-b-05 |
| domain | 安全 |
| sub | 漏洞检测与攻防 |
| type | Bugfix |
| topic | 扫描任务取消的级联传播与资源回收：父子取消、部分结果封存与终态唯一 |
| module | scan_cancellation_coordinator |
| typeName | ScanCancellationCoordinator |
| file | scan_cancellation_coordinator.py |

## 题面

值班在控制台点了取消，父任务停了，子任务还在跑，目标锁和扫描槽一个都没还；第二天同一批目标全被“目标已被占用”挡住，只能人工清锁。更麻烦的是有人连点两次取消，第二次的备注把第一次的取消原因覆盖了，审计表里查不到最初是谁、在什么时间喊的停；还有些子任务明明已经完成的检查项，随取消一起消失，事后复盘拿不出任何覆盖范围。控制台的重试按钮没有做防抖，一次误触就会连发三次取消。

这块逻辑收在 gsb/batch49-py-safety-b-05，主文件 scan_cancellation_coordinator.py，核心类型 ScanCancellationCoordinator。任务是一棵树，父任务取消沿父子关系向下级联；每个任务可以有若干检查项，完成一项记一条。每个任务记录创建顺序、终端原因与终端时刻，时间戳用一个单调递增的计数表示，便于审计比对。

保留的公开接口（签名不变）：
ScanCancellationCoordinator(config=None)
register(task_id, parent_id=None, target=None) / link(child_id, parent_id)
start(task_id) / complete_check(task_id, check_name) / finish(task_id)
cancel(task_id, reason) / request_cancel(task_id, reason) / confirm_cancel(ticket)
seal_partial(task_id) / state_of(task_id) / terminal_of(task_id) / children_of(task_id)
held_locks() / held_slots() / snapshot() / restore(state) / stats() / reset()

行为要求：
1. register 返回 pending 节点；编号重复抛 DuplicateTaskError；未知编号抛 UnknownTaskError。
2. 带 target 的任务在 start 后占用 target:<目标> 锁和 slot:<任务号> 执行槽，状态转 running。
3. link 改变父节点后 children_of 按挂接顺序返回；会形成环时抛 CycleError。
4. complete_check 按完成顺序追加检查项，同名检查项重复上报只记一次。
5. finish 把任务置为 completed 终态，terminal_of 返回 completed。
6. cancel 沿父子关系向下级联，报告里先父后子，并在同一批里把终端节点单列出来；级联深度不设上限，但同一任务在一次取消里只能被访问一次，避免环形引用重复处理。
7. 取消必须释放该任务持有的目标锁与执行槽，重复释放不得报错。
8. 取消先落终态后，后来到达的取消不得改写首次的取消原因与时间戳。
9. 父任务取消时子任务已完成的检查项要保留，封存快照里带上覆盖范围与取消原因；封存快照可以重复读取，内容不随之后的取消或恢复而变化。
10. request_cancel 只登记待确认票据，任务保持 running；confirm_cancel 才真正落取消，票据确认后再确认一次不得改动终态。
11. 取消与正常完成几乎同时到达时只保留先落的那一个终态，另一个记为 already_terminal，不得覆盖。
12. 快照包含全部节点、父子关系、锁、槽与已封存快照；stats 按 pending、running、completed、cancelled 统计。

硬约束：终态唯一且不可逆，状态迁移要互斥，两个线程并发 cancel 与 finish 不得同时落两个终态；恢复后锁与槽的持有关系必须与快照一致；执行槽总数固定，取消后槽必须回到空闲池，否则后续任务会一直排队；重复取消与重复确认都要幂等。验收：python3 -m pytest -q tests 现有 15 条必须全部通过；不得改测试、不得跳过、不得按固定输入写死；并发与重启场景自行验证；只做本地实现，不引第三方库。

## 运行方式

```bash
cd batch49-py-safety-b-05
python3 -m pytest -q tests
```

测试 15 条，全部使用标准库与本地文件，不依赖第三方包；并发与重启场景由实现自行保证。
