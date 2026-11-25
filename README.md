# batch49-py-fix-e-01

## 题面

值班同事收到扫描告警：一台宿主机上的业务容器把 /var/run/docker.sock 挂进了容器，还把宿主根目录以读写方式挂到 /host，构建流水线却一路放行；另一个容器只加了 SYS_PTRACE 就被判成严重事件。运维改完旧清单，又发现同一台容器第三次提交危险规格时仍然照常评估，隔离名单里也没有它。缺的是一层在创建前拦住危险挂载、能力膨胀与宿主命名空间共享的准入判定。入口在 gsb/batch49-py-fix-e-01 的 container_escape_guard.py，类 ContainerEscapeGuard，负责路径规范化、挂载分类、能力集合计算、命名空间评分、评估结论与隔离台账。

公开入口：ContainerEscapeGuard(*, max_mounts=16, max_findings=64, quarantine_after=3, quarantine_ttl=300, max_profiles=8, max_path_len=256)；normalize_path(path)；classify_mount(source, target, *, propagation="rprivate", read_only=False)；check_capabilities(add=(), drop=())；namespace_plan(spec)；evaluate(spec, *, now=0)；register_profile(name, spec)；profiles()；quarantine_state(key, *, now=0)；release(key, *, now=0)；findings()；snapshot()；restore(state)；stats()。参数不合法或触顶抛 ContainerEscapeError(code, detail)。

规格是字典：id 为容器键，mounts 为字典列表（source、target、read_only、propagation），cap_add 与 cap_drop 为能力名列表，host_pid、host_network、host_ipc、host_uts、host_user、host_mount 为布尔，privileged 为布尔，profile 可选。

要落地的行为：①normalize_path 去首尾空白，折叠重复斜杠与单个点段，去掉末尾斜杠（根路径除外），必须为绝对路径；相对路径、空串、含两个点的段、空白、超过 max_path_len 报 ESC_PATH_INVALID。②classify_mount 先规范化源与目标：源为 / 给 host_root；源以 .sock 结尾给 control；源为 /proc、/sys、/dev 及其子路径，或目标落在 /proc、/sys、/dev、/boot 下给 pseudo_fs；否则源落在 /etc、/root、/home、/boot、/var/lib/docker、/run/secrets 下给 sensitive；其余 safe。③可写且 propagation 为 rshared 或 shared 的挂载给 shared，只读挂载不因传播属性判 shared。④check_capabilities 把名字转大写并补 CAP_ 前缀，drop 中的 ALL 表示先清空默认基线；默认基线含 CHOWN、DAC_OVERRIDE、FOWNER、KILL、SETGID、SETUID、NET_BIND_SERVICE、NET_RAW、SYS_CHROOT、MKNOD、AUDIT_WRITE、SETFCAP；返回 effective、dangerous、allowed，前两者为升序元组，dangerous 取 effective 与高危集合（SYS_ADMIN、SYS_PTRACE、SYS_MODULE、DAC_READ_SEARCH、NET_ADMIN、SYS_BOOT、SYS_RAWIO）的交集，allowed 表示 dangerous 为空；未知能力报 ESC_CAP_UNKNOWN，同一能力同时在 add 与 drop 报 ESC_CAP_CONFLICT。⑤namespace_plan 返回 shared、score：shared 为打开共享的宿主命名空间名升序元组（pid、network、ipc、uts、user、mount），score 等于共享个数，host_pid 额外加 2、host_network 额外加 1，两者同时为真按公式累加并在评估里作为组合项升级为 critical；privileged 直接记 6。⑥evaluate 先取容器键，id 缺失或不是非空字符串报 ESC_SPEC_INVALID；挂载数超过 max_mounts 报 ESC_CAPACITY_EXCEEDED 且 detail 含 mounts 实际条数；其余字段类型不符也拒绝。带 profile 时以 profile 为底、规格同名键覆盖，未知 profile 报 ESC_PROFILE_UNKNOWN。⑦发现项：挂载按分类落 ESC_MOUNT_HOST_ROOT、ESC_MOUNT_CONTROL_SOCKET、ESC_MOUNT_PSEUDO_FS、ESC_MOUNT_SENSITIVE、ESC_MOUNT_SHARED_PROPAGATION，严重度依次 critical、critical、high、medium、high；能力给 ESC_CAP_DANGEROUS（critical），host_pid 给 ESC_HOST_PID（critical），host_network 给 ESC_HOST_NETWORK（high），其余宿主命名空间给 ESC_HOST_NAMESPACE（high），privileged 给 ESC_PRIVILEGED（critical）。每条发现含 code、severity、target、detail，挂载的 target 为容器内目标路径，非挂载为空串；相同 code 与 target 只留一条，按严重度、code、target 升序，超过 max_findings 截断并把丢弃数记入 stats 的 findings_dropped。⑧结论分级：出现 critical 给 deny，只有 high 或 medium 给 review，没有发现给 allow；返回 key、verdict、findings、quarantined、until，未隔离时 until 为 0。⑨隔离：同一容器键连续 deny 达到 quarantine_after 时，该次 quarantined 为真且 until 等于当次 now 加 quarantine_ttl；此后隔离期内任何提交（包括完全合规的规格）直接返回 verdict 为 quarantined、findings 为空，只累计 quarantined_hits；到期自动解除并清零连续拒绝计数。release 只对隔离中的键返回真并清零计数，重复 release 返回假。quarantine_state 返回 key、quarantined、until、denials。⑩台账：findings 汇总每个容器键最近一次评估的发现（覆盖写，合规评估写入空集），按 key、code、target 升序；register_profile 接受同名更新，指纹为规格规范化后 JSON 的 16 位十六进制摘要，同名同规格指纹不变，新名字超过 max_profiles 先拒绝 ESC_CAPACITY_EXCEEDED；profiles 按名字升序返回 name 与 fingerprint。⑪snapshot 深拷贝 profiles、隔离表、发现台账、计数与配置；restore 结构不符报 ESC_SNAPSHOT_INVALID 且原状态可用，成功则整体替换；stats 至少给出 evaluations、allowed、reviewed、denied、quarantined_hits、findings、findings_dropped、releases、profiles，evaluations 只统计进入判定的次数，容量与规格错误不计。⑫同一规格重复评估得到相同 findings 与 verdict，隔离推进按 ⑨ 处理，不得因调用顺序改变排布。

硬约束：evaluate、release、register_profile 与快照写入必须在同一把可重入锁内完成；多个线程同时提交同一容器键的危险规格时，denied 计数恰好停在 quarantine_after，隔离只触发一次，不得出现半套发现或重复隔离。容量判定先于写状态；快照恢复整体替换台账，失败不得破坏原状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: container_escape_guard
- typeName: ContainerEscapeGuard
