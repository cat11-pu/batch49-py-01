# batch49-py-safety-a-19

## 题面

隔离单元创建这周出了两次泄漏：一次是创建到第四个命名空间时权限不足，前面三个已经打开的 fd 没有关，跑了几百个单元后全局 fd 用尽；另一次是两个单元共用同一个命名空间，先退出的那个把 fd 关了，另一个还在使用的单元直接拿到坏描述符。事后核对时还发现，创建到一半失败的那批单元在台账里根本没有记录，无法判断哪些 fd 还开着，值班同学只能手工逐个比对进程的打开文件，夜间处置平均要四十分钟。

范围是 gsb/batch49-py-safety-a-19 的 namespace_create_rollback.py，类 NamespaceCreateRollback，负责为隔离单元一次性创建 user、pid、net、mnt、ipc、uts 六类命名空间，失败时逆序关闭已打开的 fd。六类命名空间共用一份单元台账，按单元名登记类别、fd 与共享引用。底层创建、关闭与登记分别由注入的 creator(kind)、closer(fd)、registrar(unit, fds) 完成，构造参数另有 max_ns_per_unit 与 max_fds。

公开接口：NamespaceCreateRollback(*, creator=None, closer=None, registrar=None, max_ns_per_unit=6, max_fds=32)、create(unit, *, kinds=None, exclusive=True, share_key=None)、release(unit, *, kinds=None)、list()、rollback_log()、refcount(unit, kind)、fd_table()、dup(unit, kind)、snapshot()、restore(snapshot)、stats()。创建或释放被拒统一抛 NamespaceError(code, detail)。create 返回 unit、kinds、fds、shared；release 返回本次真正关闭的 fd 数；list 每项含 unit、kinds、fds、shared、refs，refs 为每类当前引用数；rollback_log 每项含 seq、unit、kind、fd、action，action 为 close_intent 或 closed；stats 返回 created、closed、rolled_back、shared_hits、units、open_fds、close_errors。

故障与边界：①创建顺序固定为 user、pid、net、mnt、ipc、uts，只创建 kinds 点名的类别；②第 n 个失败时逆序关闭前 n-1 个 fd，抛 NS_CREATE_FAILED，detail 给出类别、errno 与已关闭 fd 列表；③kinds 缺少 user 直接抛 NS_USER_REQUIRED，不得先建其它类别；④独占单元重名抛 NS_EXISTS；⑤带 share_key 且 exclusive=False 时允许复用，同一 share_key 的 fd 与 refs 一致；⑥共享引用归零才真正关闭，重复释放返回 0 且不再调用 closer；⑦创建成功但登记失败的 fd 必须全部关闭再抛 NS_REGISTRY_FAILED；⑧单单元类别数超过 max_ns_per_unit 抛 NS_LIMIT，全局 fd 超过 max_fds 抛 FD_EXHAUSTED，两者都不得先创建；⑨同一单元并发 create 只有一个成功，其余 NS_EXISTS；⑩回滚日志先写 close_intent 再关闭，stats 的 created、closed、rolled_back 与实际一致；⑪快照含类别、fd 与共享标记，恢复后引用计数一致，结构非法抛 SNAPSHOT_INVALID；⑫释放后同一单元可重新创建并拿到新 fd。

另外三种情况也要考虑：调用方对命名空间 fd 自行 dup 后关闭原 fd，引用计数不能归零；共享命名空间的创建者退出后其他使用者仍在，不能随创建者释放；创建过程中进程被 fork，子进程继承 fd 导致关闭后仍有引用，需要按进程组统计。

约束：创建要么全成要么全清，任何失败都不得留下已打开的 fd；引用计数不得出现负值，共享组的引用与 fd 必须同增同减；同一单元的创建与释放互斥，释放期间对该单元的新建请求抛 NS_RELEASING，释放完成后台账中不得残留该单元的记录。

验收：`python3 -m pytest -q tests` 十五条必须全过；不得修改测试、跳过或对固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: namespace_create_rollback
- typeName: NamespaceCreateRollback
- file: namespace_create_rollback.py
- blind_spots:
  - 命名空间 fd 被调用方 dup 后原 fd 关闭，引用计数不能归零
  - 共享命名空间的创建者退出后其他使用者仍在，不能随创建者释放
  - 创建过程中进程被 fork，子进程继承 fd 导致关闭后仍有引用，需要按进程组统计
- summary: 多命名空间事务化创建、逆序回滚、共享引用计数与容量上限
