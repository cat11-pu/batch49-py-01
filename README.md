# batch49-py-fix-d-01

## 题面

内核模块准入上周连着出事故。一台存储节点加载了没登记的模块版本，签名对不上，加载脚本照样灌进内核，机器带病跑了六个小时；运维为了回退故障版本，把已经加载过的旧版本重新装回去，绕过了版本下限，整批节点停在了有漏洞的版本上；密钥泄露通报发出后，撤销记录也登了，但已经加载的模块没有任何一处标出来，度量结果照旧写成功。现在要把白名单、签名核验、版本下限、运行时度量与阻断收进一个组件，所有判定都能复算。

入口在 gsb/batch49-py-fix-d-01 的 kernel_module_admission.py，类 KernelModuleAdmission，拒绝统一抛 KernelModuleError(code, detail)，记录类型 VersionRule、KeyRecord、ModuleRecord、ManifestEntry、Admission。公开方法：KernelModuleAdmission(*, max_modules=32)；add_rule(name, *, min_version, max_version, allowed_keys)；remove_rule(name)；register_key(key_id, material, *, not_before=0, not_after=4102444800)；revoke_key(key_id, *, now)；expected_signature(key_id, name, version, digest)；admit(name, version, digest, signature, *, key_id, now, allow_rollback=False)；load(name, *, now)；unload(name, *, now)；measure(name, digest, *, now)；block(name, reason, *, now)；unblock(name)；retire(name)；module_of(name)；loaded_modules()；effective_manifest()；stats()；snapshot()；restore(state)。

要落地的行为：

①add_rule 校验名称非空、版本为三段数字、下限不大于上限、allowed_keys 非空且去重升序，同名重复登记给 MODULE_RULE_INVALID，remove_rule 未登记返回 False。版本按三段整数逐段比较，区间闭合；admit 未登记模块给 MODULE_NOT_ALLOWED，版本越界或版本串非法给 MODULE_VERSION_DENIED。

②register_key 要求 key_id 与 material 非空且 not_before 小于 not_after，否则 MODULE_KEY_INVALID；同 key_id 同 material 重复登记返回 False，不同 material 给 MODULE_KEY_CONFLICT；revoke_key 对未知密钥给 MODULE_KEY_UNKNOWN，重复撤销返回 False。

③签名串固定为 sha256 小写十六进制，输入按 key_id|material|name|version|digest 拼接，expected_signature 未知密钥给 MODULE_KEY_UNKNOWN。

④admit 校验顺序固定：模块、版本、digest 形态、密钥存在、密钥有效期、allowed_keys、签名。digest 必须是 sha256: 加 64 位小写十六进制，否则 MODULE_DIGEST_INVALID；密钥不在白名单给 MODULE_KEY_DENIED，now 超出窗口给 MODULE_KEY_EXPIRED，密钥已撤销给 MODULE_KEY_REVOKED；任何一步失败计入 rejected 且不落记录。

⑤admit 成功返回 Admission(accepted=True, record)，state 为 admitted，admitted_at 取 now，measures 为 0；同 name、version、digest 重复提交返回 accepted=False 且记录与首次完全一致，admitted 不增；同名但版本或 digest 不同给 MODULE_DIGEST_CONFLICT。

⑥load 只接受 admitted，未知模块给 MODULE_UNKNOWN，quarantined 或 blocked 给 MODULE_BLOCKED；重复 load 返回 False 且 loads 不增；成功后 state 转 loaded 并把 highest 提到当前版本。

⑦measure 对未知模块给 MODULE_UNKNOWN，对已登记但未加载的记录给 MODULE_NOT_LOADED；digest 相符返回 True 并累加 measures、刷新 measured_at；不符返回 False，state 转 quarantined，mismatches 加一，measures 与 measured_at 都保持原值；同一 now 上重复度量返回 False 且不重复计数。

⑧block 对已登记记录生效，未知模块给 MODULE_UNKNOWN，loaded 的先卸载并累加 unloads，state 转 blocked、blocks 加一，重复 block 返回 False；unblock 对 blocked 或 quarantined 生效并回到 admitted，不恢复 loaded，重复 unblock 返回 False。

⑨highest 记录每个模块曾加载过的最高版本，unload、block、retire 都不清零；admit 低于 highest 的版本给 MODULE_ROLLBACK_DENIED 并累加 rollbacks，显式 allow_rollback=True 放行且不计 rollbacks。

⑩retire 只允许未加载记录，否则 MODULE_BLOCKED；成功后删除记录并累加 retired，重复 retire 返回 False；max_modules 是登记记录数的硬上限，超限给 MODULE_CAPACITY_EXCEEDED 且不落记录。

⑪effective_manifest 按 name 升序返回 ManifestEntry；密钥被撤销的记录 stale 为 True 但状态不变，只有后续 load 与 measure 才按 MODULE_KEY_REVOKED 拒绝；module_of 未知给 MODULE_UNKNOWN。

⑫stats 固定返回 admitted、rejected、loads、unloads、blocks、measures、mismatches、rollbacks、key_revocations、retired 十项；snapshot 返回 version、rules、keys、modules、highest、counters，必须深拷贝，highest 随快照保存并在恢复后继续阻止降级；counters 十项齐全；restore 结构不符给 MODULE_SNAPSHOT_INVALID 且当前状态不变。

硬约束：admit、load、measure、block、retire 与计数写入必须在同一把可重入锁内完成，同一模块并发提交只能有一次 accepted=True，其余返回既有记录，admitted 只加一；快照与恢复整体替换规则、密钥、记录、highest 与计数，恢复失败不得留下半套状态；max_modules 硬顶，任何路径都不得超限。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或按固定输入写死；并发与重启场景自行验证；只做本地实现，只用标准库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: kernel_module_admission
- typeName: KernelModuleAdmission
