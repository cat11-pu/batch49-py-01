# batch49-py-game-a-15

## 题面

锻造台连着几单做出品质对不上的成品：混了两批低品质矿，成品却按平均品质算；用碎料替代整料时，剩下的零头既没退也没算进去；预占材料后取消，仓库数量和预占前对不上。更麻烦的是并发下单时两笔制作会同时看到同一份库存，结果同一块矿被扣了两次。制作组要把配方、替代材料和预占扣减统一到一个入口，涉及 gsb/batch49-py-game-a-15 下的 recipe_substitution_forge.py，类型 RecipeSubstitutionForge。它按材料分类匹配、替代折算比和批次品质档完成整单制作。

公开接口：RecipeSubstitutionForge()；add_material(material_id, *, category, quality, qty)；inventory_of(material_id)；available_of(material_id)；register_recipe(recipe_id, *, requirements, substitutes=None)；preview(recipe_id)；reserve(recipe_id)；release(reservation_id)；craft(recipe_id, *, reservation=None)；quality_of(item_id)；snapshot()；restore(snapshot)。错误统一抛 ForgeError(code, detail)。

故障与边界：
① add_material 的 category 非空、quality 在 1 到 5、qty 为正整数；同 id 同分类同品质累加数量，分类或品质不同抛 FORGE_MATERIAL_CONFLICT，参数非法抛 FORGE_INVALID_REQUEST；inventory_of 与 available_of 对未登记材料返回 0。
② register_recipe 的 requirements 是分类到正整数的非空映射；重复配方抛 FORGE_DUPLICATE_RECIPE，空映射或非正需求抛 FORGE_INVALID_REQUEST。
③ substitutes 是 材料 id 到 {category, num, den} 的映射，num、den 为正整数，指向的分类必须在 requirements 里，材料必须已登记；结构不对抛 FORGE_INVALID_REQUEST，材料不存在抛 FORGE_UNKNOWN_MATERIAL。
④ 选料顺序固定：先精确分类材料按品质升序、材料 id 升序，再替代材料按材料 id 升序；替代材料每单位折算 num/den 个等价单位，整批向下取整。
⑤ preview 不改变库存，可行时返回 feasible=True、usage（材料到实际消耗量）和空 missing；不可行时返回 feasible=False、usage={}，missing 为全部分类缺口，值取剩余缺口而不是原始需求。
⑥ 库存按 available（总量减预占）判断；精确材料不足时才用替代材料，替代材料取抵扣剩余等价量所需的最少整单位数，多出的折算零头不退料也不补足。
⑦ reserve 原子预占一次制作所需的全部材料，返回 reservation 编号与 usage，编号从 1 开始递增；材料不足抛 FORGE_INSUFFICIENT_MATERIALS 且不预占任何材料，已有预占不受影响。
⑧ craft 未带 reservation 时先预占再立即扣减；带 reservation 时只能用于同一配方，跨配方抛 FORGE_INVALID_REQUEST，未知编号抛 FORGE_UNKNOWN_RESERVATION，失败路径整单回滚。
⑨ craft 返回 item_id（item-1 起递增）、quality 和 usage；成品品质取所有投入批次中的最低品质档，不按数量加权，quality_of 按 item_id 查询，未知成品抛 FORGE_UNKNOWN_ITEM，item 编号只增不减。
⑩ release 按预占时的实际占用原样归还库存并清掉预占；未知编号抛 FORGE_UNKNOWN_RESERVATION；已消耗的预占不得再次释放。
⑪ 预占、扣减、释放可被并发调用，同一材料不得超卖；失败路径必须整单回滚，不允许扣了一半；预占账与库存账在同一把锁内更新。
⑫ snapshot 深拷贝库存、预占、配方、成品品质与编号计数；restore 结构非法抛 FORGE_SNAPSHOT_INVALID 且现场不变，恢复后预占与成品编号回到快照时的下一个可用值。

验收：python3 -m pytest -q tests 15 条必须全过；不得改测试、跳过或按固定输入写死；并发与重启恢复自行验证；只做本地实现，错误码与返回字段必须与上述名称一致。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: recipe_substitution_forge
- typeName: RecipeSubstitutionForge
