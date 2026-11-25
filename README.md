# batch49-py-safety-a-11

## 题面

受保护目录的批量置位一直靠人工脚本完成，上周的变更单把问题全暴露了：给一批文件加不可变属性，脚本跑到第七个才报错，前六个已经锁上，没人知道怎么退回去；解除时先解锁了父目录，子项还锁着，补偿脚本自己也被挡住，最后有人手工绕过，第二天发布直接改了线上配置；同一张审批单被两个作业同时执行，台账里出现重复条目；主机重启后卡在加锁与解锁中间，文件停在半锁状态。这次把这类操作收进 gsb/batch49-py-safety-a-11 的 immutable_attr_batch.py，类 ImmutableAttrBatch 负责计划、执行、回读与恢复，异常统一为 ImmutableError(code, detail)。台账用 add_path 登记模拟对象；credential 为 {"id", "actions", "paths": {路径: 摘要}}。

公开接口：ImmutableAttrBatch(*, allowed_roots, max_paths=64, max_depth=8, clock=None)；add_path(path, *, kind="file", inode=None, mount=False, fs_immutable=True, digest="", immutable=False)；attributes(path) 返回 path、kind、inode、parent、immutable、fs_immutable、mount、digest，parent 为路径上一级；credential_check(paths, credential, *, action, now=0) 返回 ok、missing、action_ok、digest_ok；plan(paths, *, action, credential=None, strict_order=False, now=0) 返回 action、ordered、skipped；set 与 unset 均为 (paths, *, credential, strict_order=False, now=0)，返回 action、applied、skipped、rolled_back；verify(path) 返回 path、inode、immutable、readback；journal_entries()；recover(*, now=0)；inject_fault(path, *, code="READBACK_MISMATCH", when="apply")；crash_after(count)；stats()。

要求逐条落地：①登记路径不在 allowed_roots 之下，plan 报 PATH_NOT_ALLOWED，detail 是该路径；②符号链接一律 SYMLINK_REFUSED；③凭证必须覆盖批次内每个路径，缺一报 CREDENTIAL_INCOMPLETE，detail 为缺失路径升序元组；④只有 set 动作的凭证不能解除，报 CREDENTIAL_ACTION_DENIED，凭证过期报 CREDENTIAL_EXPIRED；⑤置位前比对摘要与凭证，任一不符报 DIGEST_MISMATCH，detail 为不符路径升序元组；⑥目录自身置位会遮蔽未纳入同批的子项，计划阶段报 SHADOWED_CHILDREN，detail 为被遮蔽子项升序元组；⑦置位按层级由浅到深、同层按路径升序，解除必须反向由深到浅，子项未解开就解父目录会留下锁死的子项，strict_order=True 时调用方顺序与要求相反报 ORDER_VIOLATION；⑧已在目标状态的路径计入 skipped 且流水仍留一条 done，重复置位或解除都不报错；⑨批量原子：第 n 条失败时前面已生效的条目逆序补偿，已锁定的先解除再恢复原属性，rolled_back 只计这些条目，失败条目自身记为 failed，流水先落盘再执行且条目含 seq、op、path、inode、state、batch，state 取 pending、done、rolled_back、failed、cancelled；⑩每条变更都回读属性，回读不符按该条失败处理并补偿，apply 时发现 inode 与计划不一致则不置位、报 CONFLICT 且 conflicts 加一；⑪挂载点且文件系统的 fs_immutable 为假时，必须在计划阶段报 FS_UNSUPPORTED，不允许执行到一半才发现；⑫同一 inode 的两个硬链接路径同时出现在批次内，只置位一次，后一条识别为同一对象并计入 skipped；crash_after(n) 让下一次批量在第 n 条后中断且不补偿，recover 只处理最后一批未完成条目，返回 unfinished、rolled_back、conflicts。

硬约束：同一路径的 set 与 unset 并发必须串行，流水条数与实际路径操作数严格相等；路径数超过 max_paths 报 PATH_LIMIT，层级超过 max_depth 报 DEPTH_LIMIT，两者都在计划阶段拒绝；stats 返回 applied、skipped、unset、rolled_back、conflicts、recovered，applied 与 unset 只统计最终生效的变更，recover 只加 recovered 不重复计 rolled_back。

验收：`python3 -m pytest -q tests` 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: immutable_attr_batch
- typeName: ImmutableAttrBatch
