# batch49-py-game-b-05

## 题面

手游首次进关时卡在加载条十几秒，随后几个材质直接变成洋红色，日志里同一份变体被反复编译。排查指向预编译阶段：材质用到的关键字组合没枚举全，队列里还混进了重复项。第一批受影响的是三套武器材质，同一把枪在两次进入关卡时走了两条不同的编译路径，第二次又因为变体缺失退回兜底。落点在 gsb/batch49-py-game-b-05 的 shader_variant_precompiler.py，类 ShaderVariantPrecompiler，负责从材质计划生成变体清单、规范化关键字、分批下发与续跑。

公开接口：ShaderVariantPrecompiler(*, max_keywords=8, batch_size=4, total_budget=32)；normalize_keywords(keywords) 返回规范化元组；plan_from_materials(materials) 返回升序变体键；next_batch() 返回下一批键；mark_compiled(key, ok=True, error=None)；fallback_for(key)；progress()；errors()；variants()；cancel_for_material(material_id)；snapshot()、restore(state)。materials 是 Material(material_id, keywords) 的序列。拒绝时抛 ShaderVariantError(code, detail)，码为 SHADER_ 前缀。

要修的点：①关键字必须是非空字符串，同一材质里重复的关键字只算一次，去重后数量超过 max_keywords 拒绝；②材质 id 非空且不得重复，关键字字段缺失或元素不是字符串拒绝；③变体键由规范化后的关键字拼接，空关键字集合对应 default，整个清单按字典序升序去重，重复材质只产生一个变体；④变体总数超过 total_budget 直接拒绝，不截断；⑤next_batch 按清单顺序返回至多 batch_size 个尚未下发且尚未编译的键，并在内部记为已下发，重复调用不得把同一个键发两次；⑥已下发的键必须能 mark_compiled，未知键与未下发键分别报错；⑦编译失败要记录错误信息并计入失败数，不得把同一批其它键卡住，失败键在下一轮计划里仍可重试；⑧fallback_for 去掉最后一个关键字得到兜底变体，单关键字回退到 default，default 本身没有兜底；⑨progress 给出 planned、queued、compiled、failed、remaining 五项且互相自洽；⑩计划阶段某个材质被删除时，它独占且尚未编译的已下发变体可以取消，已编译产物保留给其他材质复用；⑪快照覆盖材质、清单、队列、已完成键、失败键与计数并深拷贝，恢复后 next_batch 跳过已完成键继续发，非法快照报 SHADER_SNAPSHOT_INVALID 且不改当前状态。

补充口径：变体键里的关键字按规范化顺序用竖线拼接，空集合记 default；批次之间不得交叉，每批在取批时立即占位，未编译完的键不会出现在下一批里。

硬约束：两个材质只有关键字书写顺序不同时必须折叠成同一个变体，大小写不一致则视为不同键并报格式错误；多线程取批时同一个键只能入队一次，取批与标记都在锁内完成；进程重启后按已完成键续表，不重编也不重发；关键字数、单批数量与总预算都是硬上限，超限报错而不是截断。

验收口径：`python3 -m pytest -q tests` 的 15 条用例全部通过，测试不得改动、跳过或按固定输入写死；并发取批、重启续跑与失败重试自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: shader_variant_precompiler
- typeName: ShaderVariantPrecompiler
