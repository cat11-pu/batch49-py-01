# batch49-py-safety-a-12

## 题面

发布作业改成原地改写受保护文件之后，事故明显变多：窗口开了忘关，文件在可写状态躺了两天；owner 作业被 SIGKILL，没人来得及写日志，重启后窗口还挂着；两个发布任务同时给同一路径开窗，谁都能写，回滚时按其中一份备份恢复，把另一份版本覆盖掉；到期扫描和提交撞在一起，文件既没提交也没回滚，一直是可写的；备份文件以前没人管，窗口期内被别的作业改掉，回滚把脏内容写回线上，查了两天才定位到。现在每次发布都要两个人盯着窗口，谁也不敢走开，窗口记录还得手工抄进变更单。这次把发布窗口收进 gsb/batch49-py-safety-a-12 的 immutable_window_journal.py，类 ImmutableWindowJournal 管窗口状态机、备份、定位与重启自愈，异常统一为 WindowError(code, detail)。文件与窗口都用内存台账模拟，时间为整数秒，inode 自 1 递增，备份编号固定为 bak-<窗口号>。

公开接口：ImmutableWindowJournal(*, protected_roots, max_windows=8, max_duration=600, max_retries=3, clock=None)；add_file(path, *, content="", inode=None, immutable=True)；file_info(path) 返回 path、inode、content、immutable、backup_id；open_window(path, *, owner, ttl, now=0) 返回窗口对象，含 window_id、path、owner、ttl、opened_at、deadline、state、backup_id、inode；write(path, *, window_id, writer, data, now=0)；swap(path, *, content, inode=None)；commit(window_id, *, digest, now=0)；abort(window_id, *, reason="manual", now=0)；drop_backup(window_id)；sweep(*, now)；windows(*, state=None) 按窗口号升序、可按 state 过滤；recover(*, now=0)；restore_immutable(path, *, now=0) 返回 ok、attempts、alerts；journal_entries()；inject_fault(key, *, code="READBACK_MISMATCH", times=1)；snapshot()；restore(state)；stats()。模块级 content_digest(data) 返回内容的 sha256 十六进制。

要求逐条落地：①窗口互斥与前置条件：同一路径同时只允许一个窗口，重复开报 WINDOW_EXISTS，文件当前不可变才能开窗否则 NOT_IMMUTABLE，owner 非空、ttl 为正且不超过 max_duration，窗口总数不超过 max_windows，违反分别报 INVALID_REQUEST、DURATION_LIMIT、WINDOW_LIMIT；②开窗先写 open 流水再解除不可变，deadline 等于 opened_at 加 ttl；③窗口内只有 owner 能写，其他人写入照常落盘但 violation 为真且 violations 加一，窗口关闭后再写报 WINDOW_CLOSED；④commit 比对当前内容摘要，不符报 DIGEST_MISMATCH，窗口保持 open 且仍可写，deadline 不得延长；⑤commit 成功恢复不可变、state 变 committed，abort 按备份写回内容再恢复不可变、state 变 aborted，备份缺失报 BACKUP_MISSING 且窗口保持 open；⑥恢复不可变必须先回读，回读不符就重试，超过 max_retries 记 failed 并告警一次；⑦sweep 把 now 大于等于 deadline 的 open 窗口按 abort 处理（reason 为 expired），未到期的不动，重复扫描不重复计数，与 commit 并发时同一窗口只能有一个成功；⑧snapshot 之后 restore 到新实例，recover 把所有 open 窗口 abort、已 committed 的不动，返回 scanned 与 aborted，恢复后每个涉及的路径都要回读确认属性；⑨窗口期内文件被 swap 换成新 inode 时 commit 报 CONTENT_REPLACED，不得把新文件按旧备份写回，窗口转 failed 等人工处理；⑩备份自身也要按不可变对象登记，窗口期内备份被改写必须拒绝并告警，否则 abort 会恢复出错误内容；⑪启动扫描要凭 owner 的 pid 与 start 判断存活，owner 已经不存在的窗口直接 abort，不能等超时；⑫流水条目含 seq、op、window、path、owner、state，op 取 open、commit、abort、sweep、recover。

硬约束：任何时刻文件都不得长期停留在可写状态，窗口状态机与流水跨重启一致；窗口数、窗口时长、回读重试次数都是硬顶；stats 返回 opened、committed、aborted、expired、violations、failed、recovered，abort 与到期都计入 aborted，只有到期额外计入 expired。

验收：`python3 -m pytest -q tests` 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: immutable_window_journal
- typeName: ImmutableWindowJournal
