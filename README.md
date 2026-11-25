# batch49-py-safety-a-04

## 题面

单元首进程退出后，它的子进程还在跑，注销时被当成已清理，第二天这批进程被别的服务收养，仍然读写共享卷；另一个单元重建后，旧代次的记录又被翻出来，owner 认到了新进程头上。范围是 gsb/batch49-py-safety-a-04 的 isolation_lineage_guard.py，类 IsolationLineageGuard，记录每个单元的首进程与派生谱系，扫描宿主机进程表校验 ppid 链，并识别被别的 subreaper 收养的 double-fork。ProcRow 是扫描到的一行，含 pid、ppid、starttime、state、adopted_by、pid_ns；LineageEntry 是登记进某单元的进程，含 pid、starttime、unit_id、depth、parent_pid、state。

公开入口：IsolationLineageGuard(*, max_children=32, max_depth=8)；register_unit(unit_id, *, generation=1)；register(unit_id, pid, *, ppid=None, starttime=0, now=0)；spawn(unit_id, parent_pid, child_pid, *, starttime=0, now=0)；scan(unit_id, table)；lineage(unit_id, *, include_retired=False)；retire(unit_id)；rebuild(unit_id, *, generation)；active_children(unit_id)；cross_domain(*, unit_id=None)；escape_candidates(unit_id)；owner_of(pid, starttime)；generation(unit_id)；snapshot()；restore(state)。拒绝抛 LineageError(code, detail)。

要落地的行为：①首进程登记 depth 为 0、parent 为空；派生进程 depth 等于父加一，active_children 只数活着的直接子进程，lineage 按登记顺序返回。②父进程登记在另一个单元时拒绝 LINEAGE_CROSS_DOMAIN，detail 的 owner_unit、target_unit 给出原单元与目标单元，并留下 pid、starttime 与原因；父进程查不到报 LINEAGE_PARENT_UNKNOWN。③同一个 pid 在不同 starttime 下是两个进程，按 starttime 分开记账；同一 (pid, starttime) 已被别的单元登记，后到者报 LINEAGE_ALREADY_OWNED，detail 的 owner_unit 给出先登记的单元。④每单元活跃子进程数达 max_children 拒绝 LINEAGE_QUOTA，detail 的 limit 给出上限；谱系深度超过 max_depth 拒绝 LINEAGE_DEPTH_LIMIT，扫描结果的 truncated 按到达深度上限的存活条目数计。⑤扫描遇到进程已消失按 ENOENT 处理，只计入 missing，不算违规，也不清掉登记；首进程的 ppid 在进程表里显示为 0 或 1 时按无父处理；checked 给出本次核对的存活条目数。⑥扫描发现 ppid 指向另一个单元的进程，记 ppid_cross_domain 违规，违规记录带扫描时的代次与时间点。⑦扫描发现 adopted_by 不在本单元内，识别为逃逸候选并进 escape_candidates，同时记下收养者 pid。⑧扫描把僵尸或已退出进程标为 exited，配额立刻释放，下一次扫描不再重复计数。⑨注销单元后谱系标 retired，默认查询不返回，include_retired 仍可查，注销只改状态不删条目、返回被标记的条目数；重建单元使用新一代次，返回被清掉的条目数，旧记录不复活，owner_of 对旧 (pid, starttime) 返回空。⑩快照含各单元代次、条目、跨域记录与逃逸候选，restore 后一致，非法结构报 LINEAGE_SNAPSHOT_INVALID。⑪进程在两次扫描之间完成 fork、exec 与 setsid，ppid 已变成 1，谱系只能靠登记时的父子关系补链，不能把 ppid 变化本身判成跨域。⑫同一进程被 setns 换到另一个 PID 命名空间后，/proc 里的 pid 与登记时不同，要按 starttime 与登记关系识别同一进程；单元首进程退出、子进程被 init 收养后，注销单元不能把仍存活的进程当成已清理，要在结果里单列。

硬约束：扫描与登记不出现断代，新进程要么完整可见要么完全不可见；同一 (pid, starttime) 在任一时刻只能有一个归属单元，登记与扫描并发时不得出现两个单元同时认领；扫描结果要带代次，跨代次的结果不可混用；谱系、配额与代次落盘可恢复。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发登记与扫描、重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: isolation_lineage_guard
- typeName: IsolationLineageGuard
