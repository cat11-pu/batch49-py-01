# batch49-py-safety-b-24

## 题面

攻防演练资产盘点后，安全平台的责任人路由连续被投诉：一条高优先级规则明明写着支付域归 A 组，系统却按先加的网段规则派给了 B 组；没有规则命中的机器进了待认领队列后就查不到；人工认领过的资产在规则调整时被悄悄改回规则结果，没人收到冲突提示。范围是 gsb/batch49-py-safety-b-24 的 attack_surface_ownership_router.py，类 AttackSurfaceOwnershipRouter，异常 RoutingError(code, detail)，记录类型 OwnershipRule、AssetRecord、Assignment、UnownedEntry、Conflict。

公开接口：AttackSurfaceOwnershipRouter(*, max_rules=200, max_assets=5000, unowned_retention_seconds=2592000, clock=None)；add_rule(rule_id, owner, *, priority=0, tag=None, domain_suffix=None, cidr=None, now=None)；remove_rule(rule_id)；register_asset(asset_id, *, tags=(), domain=None, ip=None, now=None)；update_asset(asset_id, *, tags=None, domain=None, ip=None, now=None)；resolve(asset_id)；claim_unowned(asset_id, owner, now=None)；release_lock(asset_id, now=None)；recompute(now=None)；unowned(now=None)；history(asset_id)；conflicts()；snapshot()；restore(state)；stats()。

故障与边界：

①规则必须且只能声明 tag、domain_suffix、cidr 之一，owner 非空，priority 为非负整数，cidr 用 ipaddress 校验；违规抛 ROUTER_INVALID_REQUEST；规则记录的 specificity 在创建时算好并随规则保存。

②规则标识重复抛 ROUTER_DUPLICATE_RULE，规则数超过 max_rules 抛 ROUTER_CAPACITY_EXCEEDED；删除不存在的规则返回 False，成功返回 True。

③资产标识为空、IP 非法、重复注册分别抛 ROUTER_INVALID_REQUEST、ROUTER_INVALID_REQUEST、ROUTER_DUPLICATE_ASSET；资产数超过 max_assets 抛 ROUTER_CAPACITY_EXCEEDED。

④多规则命中时先比 priority 大者胜，再比 specificity 大者胜，仍相同取 rule_id 字典序最小；只取一条，不得合并多个责任人。

⑤specificity 定义为：tag 规则取标签字符长度，domain_suffix 取后缀字符长度，cidr 取网段前缀位数。

⑥tag 规则要求资产标签包含该标签；domain_suffix 要求域名相等或以点加后缀结尾，不能只做字符串包含；cidr 用网段包含判断。

⑦无规则命中的资产进入待认领队列，Assignment 的 owner 与 rule_id 为 None、locked 为 False、source 为 unowned；UnownedEntry 记录 asset_id、since、last_seen，首次进入时 since 与 last_seen 都等于登记时刻，unowned 按进入时间与资产标识升序返回；超过 unowned_retention_seconds 的记录仍要能查询到，不得静默删除。

⑧claim_unowned 只接受当前仍无主的资产，成功后 owner 生效、locked 为 True、source 为 manual，并从待认领队列移除；重复认领抛 ROUTER_INVALID_TRANSITION。

⑨手工锁定的归属在 recompute 时不得被规则覆盖，只能生成 Conflict 记录并保留锁定责任人；被锁定的资产即使规则变化也不得改写 owner、rule_id 与 source；未锁定资产按当前规则重算并追加 history。

⑩release_lock 解除锁定后立即按规则重算，无规则命中则回到待认领队列；recompute 只处理未锁定资产，返回值是本次新产生的 Conflict 元组并按 asset_id 升序，没有归属变化时不追加 history；规则增删不自动重算，必须显式调用。

⑪history 按发生顺序追加，最新一条在末尾；stats 固定返回 assets、rules、assigned、unowned、claimed、recomputed、conflicts 七项，assigned 与 unowned 是当前快照计数，recomputed 累计因重算实际改变归属的资产数。

⑫快照键固定为 version、assets、rules、assignments、unowned、history、conflicts、counters，assets 与 assignments 是资产标识到记录或归属的映射，unowned 与 history 是资产标识到队列记录或归属列表的映射，rules 与 conflicts 为列表，counters 必须含七个计数键；快照必须深拷贝，键缺失、版本不符或类型错误都抛 ROUTER_SNAPSHOT_INVALID 且当前状态不变；注册、认领、解锁与重算必须持锁原子完成，时间统一为 UTC ISO-8601 秒级，除注入 clock 外不读系统时间。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与快照恢复自行验证；只做本地实现，不依赖第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: attack_surface_ownership_router
- typeName: AttackSurfaceOwnershipRouter
