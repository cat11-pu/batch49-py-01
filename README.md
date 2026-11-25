# batch49-py-safety-b-23

## 题面

季度攻防演练前，资产组把两期攻击面清单拉到一起对账，结果被运维打回：一台主机只换了 IP，报表上却出现一条新增加一条消失；上季度已销账的资产重新上线后负责人变成空的；值班同学批量点了确认，静默期内仍然每十分钟被提醒一次。范围是 gsb/batch49-py-safety-b-23 的 exposure_inventory_reconciler.py，类 ExposureInventoryReconciler，异常 ReconcileError(code, detail)，记录类型 InventoryEntry、ChangeEntry、ReconcileReport。

公开接口：ExposureInventoryReconciler(*, silence_seconds=3600, max_entries=5000, clock=None)；add_owner_rule(match_type, pattern, owner)；clear_owner_rules()；reconcile(entries, now=None)；acknowledge(asset_ids, now=None)；reminder_batch(now=None)；unacknowledged()；current_entries()；owner_of(asset_id)；last_report()；snapshot()；restore(state)；stats()。

故障与边界：

①InventoryEntry 字段为 asset_id、hostname、ip、service、owner、tags；asset_id 为空、重复、条目类型错误或标签含非字符串抛 RECON_INVALID_SNAPSHOT，条数超过 max_entries 抛 RECON_CAPACITY_EXCEEDED，非法快照不得落库。

②首次对账以空清单为基线，全部条目算新增；之后与上一期生效清单按 asset_id 对齐，输出新增、消失、变更三类，每类按 asset_id 升序，报告同时给出 baseline_count 与 current_count。

③新增与变更的 after、消失与变更的 before 都是包含 asset_id、hostname、ip、service、owner、tags 六个键的字典，fields 只列真正变化的字段并按字典序；仅 IP 变化、asset_id 相同时必须算一条变更，不能拆成一增一消。

④标签按集合比较，顺序不同或重复不产生变更；入库前标签去重并按字典序保存；service 与 hostname 按普通字符串比较。

⑤新增条目 owner 为空时按责任人规则继承，规则按添加顺序取第一条命中；显式 owner 优先于规则；match_type 支持 tag、hostname_suffix、service，分别为标签包含、域名等于后缀或以点加后缀结尾、服务名完全相等。

⑥继承结果写回生效清单，owner_of 返回生效责任人，未知资产抛 RECON_UNKNOWN_ASSET；消失条目的责任人写入记忆，同 asset_id 重新出现时恢复原责任人，不再重新走继承规则。

⑦批量确认只作用于当前快照内且本期有变更的条目，未知标识与已消失条目忽略，返回实际确认条数；重复确认同一条目返回 0，累计计数只加实际确认数。

⑧提醒只覆盖当前快照内未确认的变更条目；距上次提醒不足 silence_seconds 的不重复提醒，达到或超过后重新提醒；静默期只抑制提醒，不改变条目的确认状态。

⑨同一资产出现新的变更签名时确认状态自动失效，需重新确认；签名不变则保持已确认；确认状态只按资产标识与本次变更内容判断。

⑩快照键固定为 version、inventory、owner_memory、acknowledged、last_notified、rules、counters，inventory、owner_memory、acknowledged、last_notified 都是资产标识到值的映射，rules 为列表，counters 必须含七个计数键；快照必须深拷贝，键缺失、版本不符或类型错误都抛 RECON_SNAPSHOT_INVALID 且当前状态不变。

⑪stats 固定返回 snapshots、added、removed、changed、acknowledged、reminders、owner_rules 七项，owner_rules 是当前规则数，reminders 按实际返回的条目数累计；current_entries 按 asset_id 升序。

⑫时间统一为 UTC ISO-8601 秒级，除注入 clock 外不读系统时间；对账、批量确认与提醒领取必须持锁原子完成，同一静默期内并发提醒不得把同一条目返回两次。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与快照恢复自行验证；只做本地实现，不依赖第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: exposure_inventory_reconciler
- typeName: ExposureInventoryReconciler
