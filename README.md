# batch49-py-safety-a-01

## 题面

备份窗口里 ACL 模板下发连续翻了三次：第一次把默认 ACL 写到普通文件上，整棵目录树的权限被搅乱；第二次中途断在第九个目录，前八个已改、后面没改，没人说得清该从哪恢复；第三次运维手工补，两个任务并发命中同一批路径，审计日志对不上账。问题落在 gsb/batch49-py-safety-a-01 的 acl_template_rollout.py，类 AclTemplateRollout，用它把安全组维护的模板做成可计划、可应用、可回滚的事务。为便于本地复跑，路径到 PathAcl 的账本由 acl_store 这个映射代替内核，PathAcl 记 dev、inode、is_dir、access、default、mtime_ns。

公开入口：AclTemplateRollout(acl_store=None, *, uid_map=None, gid_map=None, max_paths=256, max_entries=64, max_depth=8, on_write=None)；add_path(path, *, dev, inode, is_dir, access=(), default=(), mtime_ns=0)；add_template(prefix, entries)；effective_template(path)；resolve_principal(kind, name)；recompute_masks(entries)；fingerprint(entries)；plan(paths)；diff(plan)；apply(plan, *, now_ns=0)；rollback(plan, *, request_id=None)；journal()；snapshot()；restore(state)；recover()。拒绝与失败统一抛 AclRolloutError(code, detail)。

要落地的行为：①模板按最长前缀命中，父子模板不叠加，子路径只吃最具体的那一份，无命中报 ACL_NO_TEMPLATE。②条目只允许 user、group、other、mask 四类；路径数、单路径条目数、路径段数分别受 max_paths、max_entries、max_depth 硬顶，段数按 / 切分后的非空段计数，越界报 ACL_PATH_LIMIT、ACL_ENTRY_LIMIT、ACL_DEPTH_LIMIT。③默认 ACL 只允许落目录，默认条目整份照抄模板、不参与掩码重算；一批里任一普通文件被模板写入 default 条目，整批拒绝 ACL_DEFAULT_ON_FILE 并在 detail 的 path 里指出路径。④命名用户与命名组必须经 uid_map、gid_map 解析成数字限定名；解析不到报 ACL_PRINCIPAL_UNKNOWN，detail 带模板行号 line 与名字 name。⑤命名条目存在而缺 mask 时，用全部命名条目权限的并集补一条 mask；已有 mask 原样保留。计划里的条目一律按 user、group、other、mask 段序、同段按限定名升序。⑥逐路径记录 dev、inode 与改前改后指纹，已与目标一致的路径不进差异；diff(plan) 返回当前已偏离计划快照的路径，刚生成计划时应为空。⑦apply 逐条先写 begin 日志再改 ACL，成功补 done；on_write 在 ACL 写完之后、补 done 之前回调，方便注入写失败；写入抛 OSError 视为该条失败，已写条目按逆序恢复原状，结果里给 failed_path、error_code 与逆序的 rolled_back；其它异常直接上抛，不得吞掉。⑧同一计划重复 apply 必须幂等：零写入、不改 mtime、applied 为空。⑨rollback 按计划逆序处理，只撤销当前指纹仍等于计划目标的条目，指纹被外部改动的一律跳过并列进 skipped。⑩两次 apply 之间进程被杀死时，重启凭 snapshot 取回日志，recover 把有 begin 无 done 的路径还原成 begin 里记录的原 ACL 并返回这些路径，已 done 的不动，恢复后日志与账本重新对齐。⑪计划生成后同一目录里改了名字、inode 未变但路径变了，不能因为路径对不上就当成新增再施加一遍。⑫两个计划并发命中同一路径时，后到者要以对方写完后的状态重算差异；回滚时目标路径若已换成另一个 inode，必须放弃该条并告警，不能把 ACL 写到新文件上。

硬约束：plan 与 apply 用 RLock 分段互斥，任意时刻读方只能看到计划前或计划后的完整 ACL；日志条目要带 plan_id、路径与改前改后指纹，且必须先落盘再动 ACL，否则杀进程后还原不了半套。验收以 python3 -m pytest -q tests 的 15 条全过为准，测试不得改动、跳过或按固定输入写死；并发与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: acl_template_rollout
- typeName: AclTemplateRollout
