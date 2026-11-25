# batch49-py-safety-b-01

## 平台字段（非题面）

| 字段 | 值 |
| --- | --- |
| name | batch49-py-safety-b-01 |
| domain | 安全 |
| sub | 漏洞检测与攻防 |
| type | Bugfix |
| topic | 检测规则 DSL 从 v2 到 v3 的批量迁移：语法改写、弃用特性降级与语义等价抽检 |
| module | rule_dsl_migrator |
| typeName | RuleDslMigrator |
| file | rule_dsl_migrator.py |

## 题面

检测平台的规则仓库正在从 v2 语法整体迁到 v3。第一批迁移结果推到预发后，告警大面积静默，排查发现迁移工具把双引号里的载荷片段也当成关键字改掉了，等于连要匹配的内容一起换；另有一批带自定义修饰符的规则整条消失，值班同学不知道漏了哪些。规则库既要能整批重跑，又要保证迁移前后语义不变，还要给审计留出改动清单。迁移工具每天凌晨批量跑一次，单批最多三千条规则，跑完把变更清单推给评审系统；一旦中途失败，第二天只能整批重来。

这部分能力放进 gsb/batch49-py-safety-b-01，主文件 rule_dsl_migrator.py，核心类型 RuleDslMigrator。规则源文本里 [rule <id>] 是规则头，一直到下一个规则头之前都算该规则正文；以 # 开头的行是注释；双引号内是字面量。特性对照表由调用方注入，键是 v2 词元，值是 v3 词元，同一份对照表会被多个批次复用。

需要保留的公开接口（签名不要改）：
RuleDslMigrator(config=None, equivalence_checker=None)
load_feature_table(table) / feature_table()
detect_version(source) / parse(source)
semantic_fingerprint(text)
migrate_rule(rule) / migrate_text(source) / migrate_batch(sources)
commit_batch(report) / committed_text(rule_id) / pending_reviews()
expand_macros(rule) / rename_macro_params(expansion)
reset()

必须落到的行为：
1. detect_version 按当前对照表判断：出现任一 v2 词元返回 v2，否则出现 v3 词元返回 v3，都没有返回 unknown。
2. parse 按出现顺序返回规则，start_line 用从 1 开始的行号。
3. 词元替换按最长匹配优先：对照表里同时有 sev 和 severity= 时，severity= 整体替换成 level=，不能留下半个词。
4. 双引号字面量和 # 注释行不参与替换。
5. 每处替换产出一条变更记录，含规则编号、从 1 开始的行号、旧词元、新词元；顺序先按行号，再按行内出现位置。
6. 迁移前后 semantic_fingerprint 必须相等；指纹忽略空白与注释差异，但对字面量内容敏感，大小写与转义写法不同也要判为不等价。
7. 已迁移文本再次迁移必须零变更，正文逐字节相同。
8. 单条规则迁移后超过 max_rule_bytes 时给出 rule_too_large 拒绝码，该条不进入已提交集合。
9. 等价抽检不通过时只回退这一条的正文和变更记录，同批其它规则照常提交。
10. 同一规则编号在一批里重复出现时整批判为不合法，不写入任何一条。
11. 宏展开后参数重名时按出现顺序改成 名字_1、名字_2，不能直接覆盖导致前一个绑定丢失。
12. 对照表里没有的 @ 修饰符必须原样保留，并把该规则编号放进待确认列表。

硬约束：commit_batch 的写入是原子的，一批要么全部可见要么全部不可见；实例会被多个线程共用，迁移过程不得让读方看到半成品；进程重启后 committed_text 只返回已提交正文；单批规则数超过 max_rules_per_batch 时整批拒绝，不做分批偷偷写入。验收：python3 -m pytest -q tests 现有 15 条必须全部通过；不得改测试、不得跳过、不得按固定输入写死；并发与重启场景自行验证；只做本地实现，不引第三方库。

## 运行方式

```bash
cd batch49-py-safety-b-01
python3 -m pytest -q tests
```

测试 15 条，全部使用标准库与本地文件，不依赖第三方包；并发与重启场景由实现自行保证。
