# batch49-py-safety-b-06

## 题面

这周同一个小版本的 libxml2 在三个来源里给出了互相打架的受影响区间：OSV 说 1.2.9 之前全部受影响，NVD 抄来一个 1.99 的上界，发行版安全公告则声明 22.04 上的 1.2.8-1 已经通过 backport 修好。值班同学手工按字符串比较，把 1.2.10 判成比 1.2.9 小，又把带 ~rc 的预发布版当成正式版放过，漏报和误报一起进了周报，同一份清单白天被三个团队各扫一遍，结论互不相同，发布评审只好全量冻结。请在 gsb/batch49-py-safety-b-06/cve_range_matcher.py 中实现 CveRangeMatcher，把多来源区间归一到统一版本序后求并集，并对单个版本给出受影响、不受影响、未知三态和原因码。

构造函数为 CveRangeMatcher(*, cache_size=128)。公开接口保持名字与关键字参数不变：add_source_ranges(cve, source, ranges, *, revision=1) -> int、set_exclusions(cve, versions) -> None、add_backport_exception(cve, *, distro, version, source=None) -> None、merge_ranges(cve) -> list、evaluate(cve, version, *, distro=None) -> RangeVerdict、evaluate_batch(cve, versions, *, distro=None) -> list、compare_versions(left, right) -> int、normalize_version(raw) -> str、sources_for(cve) -> list[str]、cache_stats() -> dict、invalidate(cve=None) -> int、snapshot() -> dict、restore(state) -> None。RangeVerdict 含 cve、version、status、reason、sources 五个字段，CveRangeError 带 code 与 detail。

故障与边界：
①normalize_version 输出 epoch:上游版本[-修订号]，缺省 epoch 记 0，无修订号不留末尾连字符；先去掉首尾空白，去空白后为空、内部含空白、缺上游段都抛 VERSION_UNPARSEABLE。
②compare_versions 按发行版口径比较，epoch 大者优先，上游段数字段比数值、字母段比字典序交替进行，修订号同一规则；预发布版与同号正式版的先后按发行版规则区分，不能按字符串字典序比较。
③merge_ranges 对同一 CVE 全部有效区间求并集，重叠或首尾相接的合并，按下界升序返回归一化串，上界 None 表示开区间。
④evaluate 区间语义左闭右开，introduced 本身算受影响，fixed 本身不算。
⑤命中返回 status 为 affected、reason 为 IN_RANGE；全部区间之外返回 not_affected 配 OUT_OF_RANGE；sources 是该 CVE 全部来源名按字典序的元组。
⑥set_exclusions 登记的版本即使落在区间内也判 EXCLUDED，排除项优先于区间。
⑦backport 例外只在声明的发行版与版本范围内生效，跨发行版不得复用，命中返回 BACKPORT_EXCEPTION。
⑧区间可带 distro 限定，判定只采纳 distro 为 None 或与查询一致的区间；从未登记过区间的 CVE 返回 unknown 配 NO_RANGES。区间为空与区间无法解析必须给出彼此不同的原因码，不能都归到未知。
⑨区间字典缺 introduced 时 add_source_ranges 抛 RANGE_MISSING_FIELD，且不得留下部分写入。
⑩同一来源用更大 revision 重新上报即来源更新，该 CVE 缓存立即失效；缓存键含 CVE、版本与发行版，容量由 cache_size 控制，cache_stats 至少给出 hits、misses、size、capacity，缓存过期只影响计算次数，不得改变判定结果。
⑪并发调用 add_source_ranges 与 evaluate 必须线程安全，合并结果与单线程一致；snapshot 返回拍摄时刻的独立副本，restore 遇结构不合法状态抛 SNAPSHOT_INVALID 且不改动当前数据。
⑫evaluate_batch 逐条等价于单独调用 evaluate，顺序与输入一致；invalidate 返回被清掉的缓存条数，批量判定结果要能逐条追溯到来源。

验收：python3 -m pytest -q tests 15 条必须全部通过；不得修改测试、不得跳过、不得按固定输入写死；并发与恢复场景自行验证；只做本地实现，不联网，不引第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: cve_range_matcher
- typeName: CveRangeMatcher
- file: cve_range_matcher.py
