# batch49-py-safety-a-26

## 题面

跨目录同步服务上线三个月后，备份线程偶尔整夜卡死，日志停在“等待第二把锁”那一行，值班同事只能重启进程；另一些时候同一线程重复进入同一目录，释放一次后别的线程立刻拿到锁，正在写的文件被改坏。更麻烦的是线程池复用工作线程之后，上一轮任务留下的可重入计数被新任务继承，释放次数对不上，锁表长期泄漏，最终把整个同步链路拖死。范围是 gsb/batch49-py-safety-a-26 的 lock_order_deadlock_guard.py，类 LockOrderDeadlockGuard，维护资源锁序表、持有计数与等待关系。

公开接口：LockOrderDeadlockGuard(max_locks_per_request=8, max_locks=64, clock=None)；register_resource(name, rank=None) 注册资源并返回锁序版本；order_version() 返回当前版本；acquire_many(resources, owner=None, timeout=0.0, mode="shared") 按全局顺序获取多把锁；release(resource, owner=None, count=1) 按次数释放；release_all(owner=None) 释放该持有者全部锁；try_upgrade(resource, owner=None) 尝试升级；held(owner=None) 返回持有计数；wait_graph() 返回等待关系；snapshot()/restore(state) 保存与恢复锁表；stats() 返回操作计数。拒绝统一抛 LockOrderError(code, detail)。

故障与边界：①同一个资源重复注册必须抛 RESOURCE_EXISTS，每次有效注册把锁序版本加一，版本只增不减，注册失败不得改动锁序；②一次请求多把锁时按全局 rank 升序获取，入参乱序不改变返回顺序与最终持有顺序；③请求中含未注册资源时整单拒绝 UNKNOWN_RESOURCE，不能先把前面几把拿到手；④同一持有者已持有高 rank 锁再请求低 rank 锁属反序依赖，抛 ORDER_VIOLATION，detail 同时给出 held 与 requested 两个资源；⑤第 n 把拿不到时必须把本次已拿到的 n-1 把全部退回，之前已持有的旧锁不受影响，等待中的线程不能看到半成品持有；⑥同一持有者重复获取同一把锁累加可重入计数，release 逐次递减并在归零时移除，返回剩余计数，对未持有锁 release 抛 NOT_HELD；⑦release_all 返回本次释放的不同资源数，重复调用返回 0，不得重复扣减统计；⑧升级只在该锁没有其他持有者时成功，失败返回 False 且原共享模式保持不变，升级一个自己不持有的锁抛 NOT_HELD；⑨等待超时抛 LOCK_TIMEOUT，detail 带资源名，并释放本次请求已经拿到的全部锁，不能留下残留持有；⑩单次请求锁数超过 max_locks_per_request 抛 TOO_MANY_LOCKS，注册资源数超过 max_locks 抛 LOCK_TABLE_FULL，两类上限都只能拒绝不能截断；⑪快照必须深拷贝，restore 接受非法结构时抛 SNAPSHOT_INVALID 且当前锁序、持有与统计完全不变；⑫stats 固定返回 requests、acquired、reentrant、released、upgrades、timeouts、violations 七项计数，每项只在对应操作成功或失败时累加一次。

并发与恢复还有几条容易漏的地方：两个线程各持一把再互相请求时，后一个反序请求必须当场拒绝，不能双方一起阻塞到超时；线程池复用工作线程时，可重入计数按持有者隔离，新任务不得继承上一任务的计数；锁序表在请求进行中被改写时，在途请求要按开始时的序表版本判定，不能把旧序持有与新序请求混成假环。

硬约束：多锁请求必须全成或全释放，持有表与计数在同一临界区内更新；快照恢复后锁序、持有计数与统计必须一致，恢复失败不得污染现场；单次请求与全局锁表都有硬顶，超限只能拒绝不能静默截断。验收：python3 -m pytest -q tests 15 条全过；不得改测试、跳过或按固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: lock_order_deadlock_guard
- typeName: LockOrderDeadlockGuard
