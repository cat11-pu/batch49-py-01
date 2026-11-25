# batch49-py-safety-a-03

## 题面

隔离单元上线后接连出事：两个单元共用一个服务号，A 的进程把 B 的临时目录读了个遍；临时目录在启动后被换成指向 /var/tmp 的链接，运行期复核仍报正常；单元声明只读挂载，实际有进程持着写句柄跑了半天。复核结果只打在日志里没落到状态上，运维重启后看到的是干净的旧结论。范围是 gsb/batch49-py-safety-a-03 的 isolation_unit_attestor.py，类 IsolationUnitAttestor，在拉起单元前逐项核验、运行期周期性复核。UnitSpec 描述 unit_id、uid、gid、附加组、临时目录、单元根、可写与只读挂载、cgroup 路径与 PID 命名空间；ProcessInfo 是宿主机扫到的一行进程；DirStat 是临时目录的属主、权限与链接目标。

公开入口：IsolationUnitAttestor(*, max_units=64)；verify(spec, *, processes=(), tmpdir_stat=None, unreadable=())；start(spec, *, processes=(), tmpdir_stat=None, unreadable=(), now=0)；recheck(unit_id, *, processes=(), tmpdir_stat=None, unreadable=(), now=0)；taint(unit_id, reason, *, conflicts=(), now=0)；reset(unit_id, reason, *, now=0)；status(unit_id)；accepting(unit_id)；draining(unit_id)；begin_recheck(unit_id) 与 end_recheck(unit_id)；match_cgroup(declared, actual)；submit(unit_id, task_id)；tasks(unit_id)；snapshot()；restore(state)；unit_count()。拒绝抛 IsolationAttestError(code, detail)。

核验项固定六条，顺序为 uid_dedicated、supplementary_groups、tmpdir、cgroup_membership、mounts、sources，每条给 ok 与 detail，detail 里用到的键固定为 pid、starttime、primary_gid、supplementary、uid、mode、symlink_target、escaped、unreadable。要落地的行为：①同一 uid 在同命名空间里被别的单元或未归属进程占用，拒绝启动 ISOLATION_UID_SHARED，detail 带冲突进程的 pid 与 starttime；同一个数字 uid 出现在另一个 PID 命名空间的单元里不算共用，按单元归属判而不是按 uid 数字判。②附加组非空拒绝 ISOLATION_SUPPLEMENTARY_GROUP，主组只记录进 detail。③临时目录属主或权限不符拒绝 ISOLATION_TMPDIR_UNSAFE，权限要 0700 这类只给属主；符号链接只有指向单元根之外时才拒绝，指向单元根内的放行。④cgroup 归属按路径分段比较，/units/u1 不能命中 /units/u1x 或 /units/u10，也不接受字符串包含式匹配。⑤可写挂载必须全部落在单元根内，越界报 ISOLATION_MOUNT_ESCAPE。⑥核验项读不到按失败处理，sources 项 ok 为假，start 报 ISOLATION_READ_FAILED。⑦运行期复核发现 uid 共用立即 taint，状态只允许 clean 与 tainted 两种，报告带 (pid, starttime)，同时拒绝新任务 ISOLATION_TAINTED、已有任务继续跑、draining 为真，taint 之后快照与状态接口读到同一个原因；submit 成功返回 queued，tasks 返回该单元已受理的任务号。⑧复核必须重新 lstat 临时目录，不能沿用启动时的结论，启动后被换成指向外部的符号链接要在复核里报出，并把新的目标路径写进 detail。⑨只读挂载被进程以写方式打开过，要从进程 fd 反查并在复核里报出对应 pid。⑩清 taint 必须显式 reset 并给出非空原因，空原因报 ISOLATION_RESET_REASON，reset 记录写进快照。⑪同一单元两次复核不得重叠，占住后第二次报 ISOLATION_RECHECK_BUSY；快照含六项核验、最后复核时间与 taint 原因，restore 后 taint 不丢，非法结构报 ISOLATION_SNAPSHOT_INVALID 且现有状态不动。⑫单元数受 max_units 硬顶，超限报 ISOLATION_UNIT_LIMIT，未登记的单元 status 返回 absent。snapshot 返回 units、tasks、resets 三个键：units 是 unit_id 到单元状态的映射，单元状态含 status、taint_reason、last_checked_at、items、conflicts；tasks 是 unit_id 到任务号列表；resets 每条形如 unit、reason、at。

硬约束：复核与标记要么都生效要么都不生效，任意时刻读方只能看到 clean 或 tainted；复核失败也要落一条带原因的记录，不能只有异常没有现场；快照恢复后 taint 与最后复核时间必须原样。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发复核与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: isolation_unit_attestor
- typeName: IsolationUnitAttestor
