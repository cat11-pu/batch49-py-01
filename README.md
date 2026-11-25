# batch49-py-safety-a-20

## 题面

宿主机上周 fd 告警，查下来是一批容器退出后命名空间引用没有释放：台账只知道 pid，进程号被新任务复用后，把别人的引用记到了新进程头上；还有一次对账把仍在运行但暂时读不到进程信息的进程全判成泄漏，运维按清单回收，误伤了两个在线业务。事后统计发现，未登记持有者和真正的泄漏混在同一张表里，值班同学没法区分该补登记还是该回收。

范围是 gsb/batch49-py-safety-a-20 的 namespace_ref_ledger.py，类 NamespaceRefLedger，负责记录每个命名空间被哪些进程以何种方式持有，周期性对账并触发回收。台账以命名空间 inode 为唯一键，多路径指向同一 inode 时合并到一条记录；持有者身份由 pid 与 starttime 共同确定，避免进程号复用造成误记。对账输入是某一采样时刻抓到的持有者列表，可能滞后于实时状态，清理时不能只看单次结果。

公开接口：NamespaceRefLedger(*, max_namespaces=32, max_holders=8, clock=None)、track(inode, pid, *, starttime, mode="fd", path=None)、release(inode, pid, *, starttime=None)、audit(observed)、leaks()、reclaim(*, now=None)、holders(inode)、refcount(inode)、state(inode)、bind_mount(inode, mountpoint)、unmount(mountpoint)、paths(inode)、snapshot()、restore(snapshot)、stats()。请求被拒统一抛 NsLedgerError(code, detail)：超命名空间数抛 NS_LIMIT，超单命名空间持有者数抛 HOLDER_LIMIT。track 返回 inode、pid、starttime、holders、paths；holders 每项含 pid、starttime、mode；audit 返回 checked、stale、unmanaged、reclaimable，stale 与 unmanaged 每项含 inode、pid；reclaim 每项含 inode、last_pid、at；bind_mount 返回该 inode 当前挂载数，unmount 返回剩余挂载数；leaks 返回 inode 升序列表；stats 返回 namespaces、holders、stale、unmanaged、reclaimable、reclaimed、mounts、audits。

故障与边界：①以 inode 为唯一键，多路径合并到同一记录，paths 去重升序；②持有者按 pid 与 starttime 区分，同一 pid 不同 starttime 算两个持有者；③重复 track 同一持有者只更新方式与路径，不增加计数；④持有者退出后转 stale，下次对账清理且不再计入 holders；⑤未登记持有者补登记并计 unmanaged（mode 记为 unmanaged），不直接判泄漏；⑥引用归零标 reclaimable，重复 release 幂等返回 0；⑦绑定挂载按挂载点计数，卸载减引用，有挂载时不判泄漏也不回收；⑧泄漏要求连续两次对账都不存在且无挂载引用；⑨全局命名空间数与单命名空间持有者数硬顶；⑩快照恢复后持有者与计数一致，stale 保留但不计数；⑪回收记录 inode、最后持有者与时间，已回收的再次回收返回空；⑫state 取值 unknown、active、reclaimable、mounted、reclaimed，查询不改变状态。

另外三种情况要能处理：持有者把 fd 传给另一个进程后原进程退出，台账只知道原 pid，需要沿传递链修正归属；命名空间 inode 在回收后被复用，旧记录不能匹配到新命名空间；进程处于 D 状态读不到进程信息时，对账应跳过而不是判为泄漏。

约束：对账与 track 并发时，新持有者要么出现在本次结果里要么完整留到下次，不得丢记录；回收必须等引用真正归零且无挂载；快照与状态要能跨进程重启恢复。

验收：`python3 -m pytest -q tests` 十五条必须全过；不得修改测试、跳过或对固定输入写死；并发与重启自行验证；只做本地实现。

台账对账时要保留命名空间之间的隔离边界，不能把不同命名空间的持有者合并。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: namespace_ref_ledger
- typeName: NamespaceRefLedger
- file: namespace_ref_ledger.py
- blind_spots:
  - 持有者把 fd 传给另一个进程后原进程退出，台账只知道原 pid，需要能沿传递链修正归属
  - 命名空间 inode 在回收后被复用，旧台账记录不能匹配到新命名空间
  - 进程处于 D 状态无法读取 /proc 信息时，对账应跳过而不是判为泄漏
- summary: 按 inode 的持有台账、两轮对账泄漏判定、挂载引用与回收
