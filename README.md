# batch49-py-safety-a-25

## 题面

共享配置仓库上周出现了双写事故。旧持有者因为垃圾回收停顿超过租约被监督方判死，新持有者拿到锁开始写，旧进程恢复后又追加了一段配置，两边都认为自己合法，最终文件被写坏。复盘要求把独占写升级成租约加 fencing：持锁者定期续期，过期后监督方判死并把单调递增的 token 授予新持有者，写入路径必须校验 token，旧持有者的落盘一律拒绝。代码在 gsb/batch49-py-safety-a-25 的 filelock_lease_fencer.py，核心类 FileLockLeaseFencer，异常统一抛 LeaseError(code, detail)。

锁状态落在 lock_path 指向的 JSON 文件里，受保护的数据追加到 lock_path 加 .data 后缀的文件；所有时间一律使用调用方传入的 now，不得读取系统墙钟。JSON 里至少保留当前 token 与租约，进程重启后要能从文件恢复，不允许把 token 归零；历史上出现过一次 token 被重置为 1 的版本，结果是旧进程的写入被误判为合法。当前文件只声明了接口，调用会直接抛 NotImplementedError，需要补齐。

公开接口：
- FileLockLeaseFencer(lock_path, lease_seconds=30, min_lease=5)
- acquire(owner, now) 返回 owner、token、expires_at、reason
- renew(owner, token, now) 返回续期后的租约
- release(owner, token)
- guarded_write(owner, token, data) 返回写入字节数
- lease() 返回当前租约或 None
- token()、remaining(now)、is_expired(now)
- history() 返回授予与抢占记录
- snapshot() 与 restore(data)

故障与边界：
1. 锁空闲时 acquire 分配 token 为 1，到期时刻为 now 加 lease_seconds，reason 为 acquired。
2. 租约仍有效时他人 acquire 抛 held，detail 里的 owner 与 expires_at 分别给出持有者与到期时刻。
3. 续期只允许当前持有者，owner 不符抛 not_owner，token 不符抛 bad_token；当前没有任何有效租约时续期抛 no_lease。
4. 续期从当前 now 重新计算到期时刻，remaining 返回距到期的秒数。
5. 租约到期后新的 acquire 直接接管，token 严格大于旧值，reason 为 preempted。
6. 主动 release 后租约清空，token 不回收，下一次 acquire 继续用更大的值。
7. 非持有者 release 抛 not_owner；release 不改变 token 计数器。
8. guarded_write 在持有者与 token 都匹配时把 data 加一个换行追加到数据文件，返回写入字节数；不匹配抛 stale_token。
9. lease_seconds 小于 min_lease 时构造即抛 lease_too_short。
10. 同一锁上并发 acquire 只能有一个成功，其余得到 held。
11. 锁文件被外部删除后，内存中的租约仍然有效到到期，续期不受影响。
12. snapshot 至少含当前 token、租约与历史；restore 后 token 不回退，下一次授予继续增大；history 每条含 token、owner、time、reason。

约束：同一把锁同时只能有一个有效租约；判死只看传入的 now 单调时钟；租约到期判定与 token 分配必须在同一临界区内完成；写入路径的校验与追加也必须在临界区内完成，避免校验通过后 token 被抢走；同一目录多进程使用时用文件锁串行化 acquire；只允许标准库。

验收：在仓库根执行 python3 -m pytest -q tests 必须全部通过；不得修改或删除用例、不得跳过、不得针对固定输入写死；并发与重启场景自行验证；只做本地实现，不引入第三方依赖。

## 运行方式

在本包目录执行：

```bash
python3 -m pytest -q tests
```

## 平台字段

- name: batch49-py-safety-a-25
- domain: 安全
- sub: 系统安全
- type: Bugfix
- topic: 文件锁租约与 fencing：心跳续期、持有者判死抢占与旧持有者写入拦截
- module: filelock_lease_fencer
- typeName: FileLockLeaseFencer
- file: filelock_lease_fencer.py
- blind_spots:
  - 持有者被 SIGSTOP 超过租约导致被抢占，恢复后必须凭 token 拒绝写入
  - 系统时间回拨时租约到期判定不能用墙钟，否则会提前或延后判死
  - 锁文件所在文件系统不支持文件锁时应在 acquire 阶段拒绝而不是降级为无锁
