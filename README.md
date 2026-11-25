# batch49-py-safety-b-09

## 题面

合规审计这周把两份材料摔在桌上：SBOM 声明里只有 zlib 一个 1.2.13，运行时清单里却加载了 libfoo，还带着另一个进程的路径；另一个服务声明了 libpng 1.6.40，实际加载的是 1.6.41；还有同事把同一个库被三个进程加载的情况报成了三条多报，审计直接质疑对账口径；值班同学按库名硬匹配，把带 ABI 后缀的 soname 全判成新组件，报表里多出几十条噪声，同一份清单在两个团队手里得出两套结论。现在要在 gsb/batch49-py-safety-b-09/sbom_runtime_reconciler.py 中实现 SbomRuntimeReconciler，按 soname、版本与路径归一后比对，产出漏报、多报与版本不符三类差异，每条差异带证据路径并能归到进程与服务。

构造函数为 SbomRuntimeReconciler(*, max_libraries=5000, equivalence_groups=None)。公开接口：load_sbom(components) -> int、load_runtime(libraries) -> int、add_equivalence_group(names) -> None、normalize_key(raw) -> str、normalize_version(raw) -> str、reconcile() -> list、findings(kind=None) -> list、summary() -> dict、evidence_for(component) -> list、service_index() -> dict、snapshot() -> dict、restore(state) -> None、reset() -> None。模型为 Component（name、version、linkage）、LoadedLibrary（soname、version、path、pid、service）、Difference（kind、component、detail、evidence、pids、services），ReconcilerError 带 code 与 detail。

故障与边界：
①normalize_key 去空白、取路径末段、转小写，含 .so 时取 .so 之前的基名；normalize_version 去空白、去前导 v、截断首个加号及其后的构建元数据，None 视为空串，归一后为空的输入直接抛 INPUT_INVALID。
②差异 kind 只有 undeclared_runtime、unused_declaration、version_mismatch 三种，reconcile 返回按 kind 再按 component 字典序排序的列表，重复对账不得让差异翻倍。
③未声明却加载的库记 undeclared_runtime，component 用归一键，evidence 为该库的全部路径，路径去重后按字典序排列。
④声明了却没加载的动态库记 unused_declaration，evidence 为 名称@版本 形式的声明串，版本用原始声明值。
⑤键相同而版本归一后不同记 version_mismatch，detail 里同时出现声明版本与实际版本，并能定位到具体路径。
⑥同一库被多个进程加载只计一次组件，不得按进程重复计数，进程与服务信息合并进同一条差异。
⑦等价替代表由 add_equivalence_group 登记，组内名字在匹配时视为同一组件；重复登记同一组只算一组。
⑧静态链接进二进制的库在运行时清单里没有，不能算作多报，这类组件不参与运行时比对。
⑨任何未登记的名字相近的包都必须留在差异里，不得自动合并或按前缀猜测。
⑩运行库条数超过 max_libraries 时抛 RUNTIME_CAPACITY_EXCEEDED，且不写入任何一条，容量判断在写入前完成。
⑪summary 至少给出 sbom_components、runtime_libraries、undeclared_runtime、unused_declaration、version_mismatch、equivalence_groups；未对账时差异计数为 0；service_index 按服务名分组、进程号升序。
⑫reconcile 可重复调用且结果稳定；snapshot 返回拍摄时刻的独立副本，restore 遇结构不合法状态抛 SNAPSHOT_INVALID，reset 清空组件、运行库、差异与等价组，恢复后可以继续追加装载并重新对账。

对账口径要能对审计复述：每条差异为什么出现、证据在哪，差异要能导出给审计逐条复核。

验收：python3 -m pytest -q tests 15 条必须全部通过；不得修改测试、不得跳过、不得按固定输入写死；只做本地实现，不联网，不引第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: sbom_runtime_reconciler
- typeName: SbomRuntimeReconciler
- file: sbom_runtime_reconciler.py
