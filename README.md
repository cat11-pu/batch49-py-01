# batch49-py-safety-a-16

## 题面

临时目录清理作业本周连出三起事故：一次删到一半机器重启，第二天重跑时把已删条目又记了一遍，进度对不上；一次运维把待清理目录改名挪走，清理器仍按旧路径继续删，误伤了同名新目录；还有一次删到中途磁盘写满，执行记录写不进去，清理器没有停下，最后连删到哪都说不清。值班同学只能靠人工比对目录列表猜测进度，交接班时说不清哪些已经删掉、哪些还没动过。

范围是 gsb/batch49-py-safety-a-16 的 temp_delete_resumer.py，类 TempDeleteResumer，负责为超大目录的删除建立可续跑计划，并按计划逐项执行。构造时由调用方注入 probe(path)、listdir(path)、remove(path) 三个回调，可选传入 log_writer(record) 做持久化记录，便于崩溃后还原现场；其余构造参数为 root、max_depth、max_items、subplan_size，分别限制起点、递归深度、条目规模与子计划粒度。

公开接口：TempDeleteResumer(*, root, probe, listdir, remove, log_writer=None, max_depth=8, max_items=10000, subplan_size=64)、plan()、entries()、subplans()、resume()、status()、cancel(reason="")、pending()、log_records()、snapshot()、restore(snapshot)、classify_error(errno_value)。计划无法建立或执行被拒时统一抛 DeleteResumeError(code, detail)。plan 返回 root、entries、skipped、subplans、state 五个字段；entries 每项含 seq、path、dev、inode、depth、kind；subplans 每项含 index、first_seq、last_seq、count；resume 返回 state、deleted、remaining、skipped、replaced、busy、errors，status 在此基础上多一个 planned，remaining 为尚未成功删除且未跳过或替换的条目数（busy 与失败计入）；errors 每项含 seq、path、code；replaced 只记录 inode 不符的那一条、不展开子项；取消后的状态为 cancelled；快照包含 plan 的 state、root、entries 与所有已处理条目。

故障与边界：①计划条目逐项记录 path、dev、inode、depth，子项先于父目录、同级按名称升序，seq 从 1 连续；②树深超过 max_depth 抛 DEPTH_LIMIT，条目总数超过 max_items 抛 ITEM_LIMIT；③条目数超过 subplan_size 时按 seq 顺序拆成子计划；④执行前复核每个条目的 dev 与 inode，不符的条目标 replaced 且整棵子树不执行；⑤执行时先写 intent 记录再调 remove，成功后补 deleted；⑥取消后已删不回滚、未删保留，再次 resume 从剩余条目继续；⑦目录现场比计划多出子项时标 busy 且不删该目录，剩余条目数仍计入；⑧符号链接与挂载点不跟随、只记 skipped；⑨并发 resume 只允许一个执行者，其余立即返回 running；⑩全部完成后再次 resume 幂等返回 done 且 deleted 为 0；⑪单条失败按 errno 分类为 permission、busy、missing、other 并记录后继续其余条目；⑫快照深拷贝，结构非法抛 PLAN_INVALID 且原状态不变。

另有三种现场情况必须一并考虑：执行期间持久化记录写不进去（例如磁盘写满）时，第一动作是停止删除，不能继续删到无法恢复；目录中途被 rename 到计划外路径，按 inode 仍能认回对象，但进度记录里的相对路径已经失效，需要单独重新定位；删除的文件若在别处还有硬链接，本次删除不能按空间已释放计数。

约束：同一计划任一时刻只有一个执行者，先到者持锁执行，后来者不得并发删除；日志顺序必须与实际删除顺序一致，每写入一对 intent 与 deleted 才代表该对象真正消失，不得重复删除计划外的新对象；进度、取消状态与快照要能跨进程重启恢复，恢复后计数不得倒退。

验收：`python3 -m pytest -q tests` 十五条必须全过；不得修改测试、跳过或对固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: temp_delete_resumer
- typeName: TempDeleteResumer
- file: temp_delete_resumer.py
- blind_spots:
  - 目录在中途被 rename 到计划外路径，按 inode 仍能找回，但进度日志里的相对路径全部失效
  - 硬链接文件在另一路径仍被引用，删除本路径不应算作空间已释放
  - 计划执行期间磁盘满导致日志写不进，必须先停止删除而不是继续删到无法恢复
- summary: 可续跑删除的计划、inode 身份复核、逐项日志与取消/幂等语义
