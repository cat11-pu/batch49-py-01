# batch49-py-safety-a-02

## 题面

共享盘迁移后运维照着半年前的快照回灌 ACL，结果三条路径被写成 nobody，两条换了 inode 的目录把新文件的权限盖掉，报告里只有一句恢复完成。快照记了 dev 与 inode，目标机却按路径对齐，属主和两套 ACL 各自错位；重试一次又从第一条重新写，已经恢复好的条目被反复改掉修改时间。范围是 gsb/batch49-py-safety-a-02 的 acl_snapshot_reconciler.py，类 AclSnapshotReconciler，负责子树快照的导出、比对与可续跑恢复；路径的当前状态由 put、drop 维护，Record 记 path、dev、inode、owner_uid、owner_gid、access、default、is_dir、symlink，ACL 条目用 tag:限定名:权限 的规范字符串。

公开入口：AclSnapshotReconciler(*, max_conflicts=8, known_uids=None, known_gids=None, symlink_policy="link", on_write=None)；put(path, *, dev, inode, owner_uid, owner_gid, access=(), default=(), is_dir=True, symlink=None)；drop(path)；snapshot(root)；export_bytes(snap)；import_bytes(data)；compare(snap)；restore(snap, *, allow_create=False, policy="keep")；resolve_conflict(path, *, strategy)；merge_entries(desired, current, policy)；fingerprint(record)；journal()；resume()；acquire_segment(segment)；release_segment(segment)；save_state()；load_state(state)。拒绝抛 AclSnapshotError(code, detail)。

要落地的行为：①同一子树连续导出两次，export_bytes 逐字节一致，digest 等于导出字节的 sha256，import_bytes 能读回同一条目序列。②快照按路径升序，逐条带 dev、inode、属主与两套 ACL；指纹要能区分 access 与 default 两套内容。③同一 inode 被硬链接到两个路径而快照只记了其中一个时，恢复不得把 ACL 施加到未记录的路径上。④符号链接路径恢复要明确作用于链接本身还是目标，并把选择写进报告，不能两种行为随机漂移。⑤导出期间子树被并发改动时，导出的指纹要能识别为不稳定并重试，不能把半新半旧的条目当成一致。⑥inode 相同且两套 ACL 都未变，直接跳过，不产生写入。⑦路径存在但 inode 变了，标 conflicted，原因 INODE_CHANGED，绝不覆盖。⑧快照有而目标缺失，默认只记 skipped 不创建；allow_create 为真才建，建后用快照里的 ACL 与属主。⑨目标多出的条目按 keep、merge、replace 处理，默认 keep：keep 是额外条目保留、快照里的同名条目覆盖目标值，merge 是额外条目保留、同名权限取并集，replace 是结果与快照逐条一致。⑩access 与 default 分开比对与写回：只有 access 不同就只改 access，default 原样保留；只有 default 不同就反过来，两套互不影响。⑪预检集中判定：compare 只做判定、不写目标账本；冲突条数超过 max_conflicts 整批中止并报 ACL_CONFLICT_LIMIT，一条都不写，detail 带冲突条数与上限；属主 uid 或 gid 不在目标机 known_uids、known_gids 里的条目标 OWNER_UNKNOWN，不许写成 65534；每条结论带 path、reason、current、snapshot 四个值。⑫写入中途失败时报告标 interrupted 并留下已开始未完成的位置，日志与待办可由 save_state、load_state 搬到新实例，resume 接着做完且已完成的不重写；恢复按顶层路径分段互斥，acquire_segment 占住某段后 restore 报 ACL_SEGMENT_BUSY 且不写，同一路径在快照与恢复期间只允许一个写入者。

硬约束：恢复要么凭日志续跑到底，要么整体不生效；冲突预检必须先于任何写入，进程被杀后重启靠日志与待办续跑；同一 inode 的多个硬链接路径在恢复期间只允许一个写入者，冲突决策要留痕，事后能说清哪条被谁改过。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启续跑自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: acl_snapshot_reconciler
- typeName: AclSnapshotReconciler
