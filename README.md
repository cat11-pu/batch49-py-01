# batch49-py-game-a-22

## 题面

合服第二天，公会管理后台的越权记录铺了一整页：副会长把会长从成员列表里踢了出去，普通成员拿着上周就该过期的临时授权改了公会公告，客服想按时间复盘，却看到审计日志顺序是乱的、改过一次还能被改回去。范围是 gsb/batch49-py-game-a-22 的 guild_rank_authority.py，类型 GuildRankAuthority，负责职位权限矩阵、临时授权、到期回收与审计。

公开接口：found_guild(master_id)、add_member(member_id, rank)、set_rank_permissions(rank, permissions)、set_member_rank(actor_id, member_id, rank)、grant_temp(grantor_id, target_id, permissions, expires_at, *, now=0)、revoke(actor_id, grant_id)、check(member_id, permission, *, at=0)、expire_grants(now)、active_grants(member_id, *, at=0)、audit_log()、transfer_master(actor_id, new_master_id, *, now=0)、snapshot()、restore(state)。拒绝时抛 GuildError(code)；职级为 1 到 max_rank 的整数，1 权限最少、max_rank 为会长。

要修的点：①建会只允许一次，重复报 GUILD_ALREADY_FOUNDED；加成员同 id 重复报 GUILD_DUPLICATE，职级不在 1 到 max_rank 报 GUILD_BAD_RANK；②set_rank_permissions 覆盖该职级自身权限，职级越界报 GUILD_BAD_RANK，有效权限等于 1 到该职级自身权限的并集，改表后立刻生效、不需要重新加成员；③check 未知成员报 GUILD_NO_MEMBER，命中返回 True、否则 False；④set_member_rank 要求操作者职级严格高于目标当前职级、目标新职级严格低于操作者当前职级，否则 GUILD_RANK_TOO_LOW，成功返回 member 与 rank；⑤grant_temp 的授权者必须自身持有被授出的权限，整套都不持有报 GUILD_DENIED，expires_at 不大于 now 报 GUILD_BAD_EXPIRY，成功返回 grant_id、granted（按权限名升序）、expires_at，grant_id 形如 g1、g2 按发放顺序单调递增；⑥临时授权不能突破授权者自身的权限上限，超出部分静默截断，不报错也不记假权限；⑦check 的时间语义：at 达到 expires_at 即失效，未到期但已被撤的同样失效；⑧revoke 只有授权者本人或会长能撤，否则 GUILD_DENIED，未知 grant_id 报 GUILD_NO_GRANT，成功返回 True；⑨目标成员被降级后，其已经发出的临时授权立即失效，不用等 expire_grants 扫到；⑩expire_grants 回收 expires_at 不大于 now 的授权并返回条数，active_grants 按 grant_id 升序返回未失效授权，GrantRecord 含 grant_id、grantor、target、permissions、issued_at、expires_at、frozen；⑪审计日志按发生顺序追加，每条含 seq（从 1 起）、action、actor、detail，audit_log 返回副本、外部修改不影响内部，只记建会、加成员、改职级、授权、撤销、回收、转让七类事件，权限表配置本身不记；⑫transfer_master 只有会长能发起否则 GUILD_DENIED，成功后新会长职级为 max_rank、原会长降一级，返回 master 与 previous_master，原会长已发出的临时授权保留到各自到期，但不可再新授权。

硬约束：单会同时在效的临时授权条数受 max_temp_grants 硬顶，超限报 GUILD_GRANT_CAP、已发放的不受影响；check、revoke、expire_grants 会被多个网关协程并发调用，同一成员的权限判定必须看到某一完整状态，不能读到撤了一半的授权表；进程重启后凭 snapshot 与 restore 恢复，成员职级、权限表、授权、审计必须逐项对上，快照顶层同时有 members 与 grants 两键，结构缺键或类型不符报 GUILD_SNAPSHOT_INVALID 且不破坏当前状态。

验收：`python3 -m pytest -q tests` 的 15 条必须全过，测试不许改、不许跳过、不许按固定输入写死；并发与重启场景自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: guild_rank_authority
- typeName: GuildRankAuthority
