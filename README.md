# batch49-py-safety-a-27

## 题面

主机加固脚本在容器宿主机上执行绑定挂载时连出事故：目标目录原本挂着数据盘，脚本直接覆盖后原数据在宿主机上不可见，业务回滚只能重启机器；同源同目标的自绑定把目录挂成空目录，日志却显示成功；校验收到的路径和目标真实路径不一致，挂载点跑到符号链接指向的别处，事后排查找不到落点。运维还发现并发执行时两个任务同时挂同一个目标，一个成功一个只挂了一半，进程退出后目录结构残留，下一次巡检直接报错。范围是 gsb/batch49-py-safety-a-27 的 mount_target_validator.py，类 MountTargetValidator，负责绑定挂载前的校验、执行、卸载与挂载栈记账。

公开接口：MountTargetValidator(allowed_source_roots=("/srv",), max_mounts=8, max_depth=4, bind_supported=True, clock=None)；resolve(path) 解析真实路径；validate(source, target, recursive=False, read_only=False, overmount=False, allow_create=False) 返回校验结果与 receipt；apply(source, target, recursive=False, read_only=False, overmount=False, allow_create=False, receipt=None) 执行并返回挂载记录；unmount(target) 卸载本模块记录；is_mounted(target) 判断是否已挂载；mounts() 返回挂载栈；report() 汇总；snapshot()/restore(state) 保存恢复；stats() 返回计数。拒绝统一抛 MountCheckError(code, detail)。

故障与边界：①源不存在或不是目录抛 SOURCE_MISSING，源解析后落在 allowed_source_roots 之外抛 SOURCE_OUTSIDE_ROOTS，宿主机不支持绑定挂载时在 validate 阶段抛 BIND_UNSUPPORTED，且不得创建目标目录；②目标不存在时只有 allow_create=True 才允许创建，apply 记录里标 created_target，否则抛 TARGET_MISSING；③源与目标解析后同设备同 inode 抛 SELF_BIND，不能把目录挂到自己；④目标已被本模块挂载时 validate 抛 OVERMOUNT_REQUIRED，只有显式 overmount=True 才允许覆盖，新记录写 covered_source 并在 report 里计入覆盖次数；⑤recursive 与 read_only 必须显式传入，缺省按非递归、可写记录，递归源相对允许根超过 max_depth 抛 DEPTH_EXCEEDED；⑥validate 返回的 receipt 记录源与目标的设备号、inode，apply 带 receipt 时若目标 inode 已变化抛 TARGET_CHANGED，源侧变化同样要重新校验而不是沿用旧结果；⑦挂载记录数达到 max_mounts 抛 CAPACITY_EXCEEDED，apply 失败必须删掉本次创建的目标目录，不能留下空目录或半挂载；⑧同一目标并发 apply 只允许一个成功，其余抛 TARGET_BUSY，挂载栈里只能出现一条该目标记录；⑨unmount 只卸载本模块记录的挂载，按后进先出揭出被覆盖记录，未记录的目标抛 NOT_MOUNTED；⑩report 给出挂载点、覆盖次数与解析后路径，stats 固定返回 validated、applied、unmounted、rejected 四项计数。

还有三条边界必须处理：目标路径中间某级是符号链接时，解析后的真正挂载点与字面路径不同，报告必须写解析后路径；目标属于其他命名空间共享的挂载点时，本命名空间内卸载会影响其他使用者，不能静默执行；源目录在 validate 之后被删除重建时，apply 要按新 inode 重新校验而不是沿用旧结果。

硬约束：校验与执行之间必须识别状态变化，失败的 apply 不留目录或半挂载，挂载栈与统计同时回滚；快照恢复后挂载栈、覆盖关系与统计一致，恢复失败不得改动现场；挂载点数量与递归深度都有硬顶。验收：python3 -m pytest -q tests 15 条全过；不得改测试、跳过或按固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: mount_target_validator
- typeName: MountTargetValidator
