# batch49-py-safety-b-11

## 题面

补丁合规平台上周连出两起事故。第一起是一批需要前置修复的安全补丁被塞进同一个批次，装到第三台机器时前置缺失，整批回滚，业务停机四十多分钟；第二起是同一批里两个补丁都在改 net/epoll.c，平台照样并排下发，后装的那个把先装的改动整段覆盖，值班同事只能手工比对回退。工单系统现在要求平台在排批次之前先把前置关系、基线版本和同文件冲突算清楚，并把装不上的补丁整理成阻塞链交回，而不是笼统报一句失败。运维同事反馈，现在每排一次批次都要人工翻一遍依赖清单，一个资产五十多个补丁，光核对就要半小时，还经常漏。

改动范围限定在 gsb/batch49-py-safety-b-11 的 patch_precondition_planner.py，类型 PatchPreconditionPlanner。对外接口：PatchPreconditionPlanner(*, max_batch_size=8, max_patches=512)；add_asset(asset_id, version)；asset_version(asset_id)；add_patch(patch)；base_version_ok(asset_id, patch_id)；dependency_closure(patch_id)；conflicts(left_id, right_id)；plan(asset_id, patch_ids)；minimum_blocking_set(asset_id, patch_ids)；cycle_members(patch_ids=None)；snapshot()；restore(state)；stats()。Patch 用 dataclass 承载 patch_id、files、requires、base_version、hunks 五个字段，hunks 是（文件名、起始行、结束行）三元组序列；PlanResult 返回 asset_id、batches、blocked、reasons。

要修的点：

① 前置补丁必须落在更早的批次，同一批次内任意两个补丁不得改同一文件。
② 每轮按 patch_id 升序扫描就绪集合，能放进当前批次就放，冲突或批次已满则顺延到下一轮，max_batch_size 是硬上限，排序只按 patch_id 字典序，不看登记先后。
③ 基线版本与资产当前版本不一致的补丁不进批次，进 blocked，reasons 里记 BASE_VERSION_MISMATCH。
④ 前置补丁自己被阻塞时，依赖它的补丁一并阻塞，reasons 里记 PRECONDITION_BLOCKED。
⑤ 候选集合缺少某个前置补丁直接报 PATCH_PRECONDITION_MISSING，detail 带缺失的补丁号，不做半套计划。
⑥ 前置关系成环报 PATCH_CYCLE；cycle_members 要返回环上全部成员，只报第一个节点算不合格。
⑦ 两个补丁改同一文件时一律按冲突处理，即使 hunks 区间不相交也要分到不同批次，并把 MANUAL_REVIEW 记进 reasons 交人工确认。
⑧ 同一 patch_id 重复登记报 PATCH_DUPLICATE，空 patch_id 或空文件清单报 PATCH_INVALID。
⑨ 未知资产在 asset_version 与 plan 上报 ASSET_UNKNOWN；候选集合为空返回空计划而不是报错。
⑩ 完全不可满足时 minimum_blocking_set 只交回造成阻塞的根节点，按升序输出，把所有冲突都倒出来视为不合格。
⑪ 登记补丁总数超过 max_patches 报 PATCH_CAPACITY_EXCEEDED，不静默淘汰最旧的。
⑫ 快照恢复后资产版本、补丁表、累计计数都在，恢复不共享原结构，恢复出来的实例要能直接接着出计划。

硬约束：plan 结果与候选集合传入顺序无关，同一资产同一集合任意次调用返回逐字段一致的计划；add_patch 与 plan 会被多个工单协程同时调用，持锁后不得看到半张补丁表；快照与恢复要能跨进程重建排批次能力。stats 返回 assets、patches、plans、blocked 四个累计计数，plans 只统计成功返回计划的次数，blocked 累计所有调用里被判阻塞的补丁条数。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，不改测试、不跳过、不按固定输入写死；并发登记与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: patch_precondition_planner
- typeName: PatchPreconditionPlanner
