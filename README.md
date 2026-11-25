# batch49-py-game-a-01

## 题面

客服转来一批黑屏工单：老玩家把 1.0 时代的存档拷进新客户端，进游戏直接黑屏；换回旧客户端，刚存的新档又读不出来。同一批工单里还有更麻烦的情况：有人手动改过存档里的金币数，进游戏后整份存档被判坏，连账号下另一个完好的槽位也一起读不出来，玩家只能重开。范围锁定 gsb/batch49-py-game-a-01 的 save_migration_chain.py，类型 SaveMigrationChain，负责按存档 schema 版本把每个槽位逐级迁到当前版本，迁移前后都能核对内容校验和，任何一步出问题都退回原样，坏档只影响自己那一格。

存档按账号分目录，每个账号固定几个槽位，槽位里放一份 JSON 内容和它记录的版本号；迁移器由策划按版本逐级提供，每级只认相邻的下一级，不允许一个迁移器顺手把 1.0 直接拉到 4.0。运维还需要能看清某个槽位当前停在哪一版、上一次迁移跨了几级、备份还在不在。

公开接口：SaveMigrationChain(*, current_version, slots_per_account=3)；register_migration(from_version, to_version, migrator)；create_slot(account, slot_id, payload, *, version=1, checksum=None)；plan_upgrade(account, slot_id)；migrate_slot(account, slot_id, *, target_version=None)；last_migration(account, slot_id)；rollback_slot(account, slot_id)；verify_slot(account, slot_id)；list_slots(account)；slot_payload(account, slot_id)；set_active_slot(account, slot_id)；active_slot(account)；snapshot()、restore(state)、stats()；模块级 payload_checksum(payload)。SlotMeta 含 account、slot_id、version、checksum、status、size 六个字段，MigrationSummary 含 account、slot_id、from_version、to_version、steps，stats 返回 slots、migrations、steps、rollbacks、verifications、repairs 六项，其中 migrations 统计成功提交次数，steps 统计实际执行的迁移器步数。拒绝一律抛 SaveError(code, detail)。

要查清的现象与边界：① 迁移器只能登记相邻两级，跳级登记报 MIGRATION_JUMP，同一 from 重复登记报 MIGRATION_DUPLICATE；② 每账号槽位数按 slots_per_account 硬顶，超限报 SLOT_CAPACITY 且不动已有槽位；③ 校验和取规范化 JSON（键排序、无多余空白、UTF-8）的 SHA-256 前 16 位小写十六进制，create_slot 传入 checksum 时原样记录、留给 verify_slot 判定，不因为对不上就拒绝建槽，槽位同时记录规范化 JSON 的字节数；④ plan_upgrade 返回 (from_version, to_version) 元组列表，从槽位当前版本逐级列到 current_version，已是最新返回空列表；⑤ migrate_slot 按链逐级执行，每步把 payload 与 from_version 交给迁移器，全部成功后才提交新版本、重算校验和并把状态恢复为 ok，中途失败不允许留下半迁移内容；⑥ 已是最新版本的槽位重复调用 migrate_slot 直接返回同一份元数据，迁移器不得再次执行；⑦ 任一迁移器抛异常都包装成 MIGRATION_FAILED，槽位内容、版本与校验和保持原样，状态置为 needs_repair；⑧ rollback_slot 恢复到最近一次迁移前的备份，备份只留一份，再次回滚报 SLOT_NO_BACKUP；⑨ verify_slot 重算校验和，返回真假并同步状态为 ok 或 corrupt，列表里 corrupt 的槽位不得阻止同账号其他槽位继续迁移；⑩ list_slots 按 slot_id 升序且只含本账号槽位，空账号返回空列表；⑪ 迁移目标版本低于槽位当前版本时拒绝，内容与校验和保持原样；⑫ 跨度超过一级但中间版本没有登记迁移器时整链拒绝，不允许跳到最近的可用版本。

硬约束：同一槽位的并发迁移必须串行，八路同时调用后每个迁移器各执行一次、结果一致，不同账号之间互不阻塞；快照是深拷贝，恢复后槽位内容、版本、校验和、状态、备份与 active 标记都在，结构不全报 SNAPSHOT_INVALID 且原状态不变；每账号槽位数量是硬上限，备份只保留一份也不额外占槽位。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: save_migration_chain
- typeName: SaveMigrationChain
