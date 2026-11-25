# batch49-py-safety-b-07

## 题面

版本治理小组这周被三个生态同时投诉：灰度清单里 1.10.0 被排到 1.9.0 后面，安全修复顺序整个颠倒；同一个修复版本在 Python 包里写成 1.2.3rc1，在容器镜像里写成 1.0.0-alpha.2，在基础镜像里又带 Debian 的 epoch 和修订号，值班同学直接按字符串排序，预发布版压过了正式版，修订号也排反；同一个写错的版本串在两个生态下解析失败，缓存却只记一条，排查时找不到源头。上周灰度因此回退了两次，发布窗口全部错过，负责人要求先把排序口径统一。请在 gsb/batch49-py-safety-b-07/version_order_registry.py 中实现 VersionOrderRegistry，按生态选择解析器把版本串变成可比较键，生态内比较满足全序，跨生态比较必须先声明换算关系。

构造函数为 VersionOrderRegistry(*, cache_capacity=64)。公开接口：register_ecosystem(name, parser)、declare_bridge(source_ecosystem, target_ecosystem, *, translator)、parse(ecosystem, raw) -> ParsedVersion、compare(ecosystem, left, right) -> int、compare_cross(left_ecosystem, left, right_ecosystem, right) -> int、sort_versions(ecosystem, raws) -> list、dedupe(ecosystem, raws) -> list、ecosystems() -> list、cache_stats() -> dict、clear_cache(ecosystem=None) -> int、snapshot() -> dict、restore(state) -> None。ParsedVersion 含 ecosystem、raw、valid、key 四个字段，VersionOrderError 带 code 与 detail。

故障与边界：
①内置生态为 pep440、semver、debian，ecosystems 按字典序返回；register_ecosystem 接受 raw 到元组的可调用对象，解析器抛 ValueError 视为无法解析，自定义生态不得覆盖内置生态。
②parse 在无法解析时 valid 为 False、key 为空元组，不抛异常；未知生态抛 ECOSYSTEM_UNKNOWN。
③compare 只在两侧都可解析时返回 -1、0、1，任一侧无法解析抛 VERSION_INVALID，比较必须反对称且传递。
④pep440 发布段按整数比较且末尾零段等价，dev 低于预发布，预发布（a/b/rc）低于同号正式版，post 高于正式版。
⑤semver 主次修订按数值比较，预发布后缀低于同号正式版，标识按点分段、数字段按数值，构建元数据不参与优先级比较但去重时仍视为不同版本。
⑥debian 的 epoch 大者优先，上游段数字与字母交替比较，波浪号低于空，修订号遵循同一规则。
⑦sort_versions 升序返回原始串，无法解析的集中到末尾并保持它们之间的原输入次序，合法项之间的排序结果不依赖输入顺序，排序结果可直接用于灰度批次编号。
⑧dedupe 按解析键去重，保留首次出现的原始串与次序，非法项按原串去重后原样保留。
⑨compare_cross 在未声明换算关系时抛 BRIDGE_MISSING；declare_bridge 声明把左侧生态的串换算到右侧生态，声明后按目标生态口径比较，没有声明不许猜。
⑩缓存容量由 cache_capacity 控制，键包含生态与原始串，容量满时淘汰最早写入的条目，cache_stats 至少给出 hits、misses、size、capacity，缓存命中与否不改变比较结果。
⑪clear_cache 清理指定生态或全部并返回清除条数；snapshot 返回拍摄时刻的独立副本，副本包含已注册生态、换算关系与缓存容量，restore 遇结构不合法状态抛 SNAPSHOT_INVALID 且不改动现状，恢复后旧快照仍可读。

全序与顺序稳定性按上面口径自行验证；缓存容量是硬上限，任何时刻都不得超出。

验收：python3 -m pytest -q tests 15 条必须全部通过；不得修改测试、不得跳过、不得按固定输入写死；只做本地实现，不联网，不引第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: version_order_registry
- typeName: VersionOrderRegistry
- file: version_order_registry.py
