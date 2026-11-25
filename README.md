# batch49-py-safety-b-27

## 题面

漏洞工单这周被数据组退回：三家扫描器导出的结果字段各不相同，同一份报告里 severity 一会儿是 severe 一会儿是 moderate，值班同学按字面排序把 warning 当成高危先处理；有的条目带了 CVSS 向量却算不出分，有的向量写错一个字母就被系统记成 0 分，风险直接被抹平；文件路径混着反斜杠、大小写和 ./../，同一条漏洞在台账里被当成三个文件。更麻烦的是有人把一版结果按自己的字段名另存，第二天重跑时对不上游标，前一天的接收记录和拒绝原因都找不回来。范围是 gsb/batch49-py-safety-b-27 的 scan_result_normalizer.py，类型 ScanResultNormalizer，负责把各来源记录归一成统一字段。

公开接口：ScanResultNormalizer()；map_severity(word)；parse_cvss(vector)；normalize_path(path)；normalize_record(record)；normalize_batch(records, *, batch_id, limit=None)；resume(batch_id, records)；normalized(batch_id)；rejections(batch_id)；batch_state(batch_id)；checkpoint(batch_id)；stats()；snapshot()；restore(state)。拒绝操作抛 NormalizeError(code, detail)。

故障与边界：①严重度词表 critical/crit/blocker 归 critical，high/severe/major 归 high，medium/moderate/warning/warn 归 medium，low/minor 归 low，info/informational/note/trivial 归 info，匹配前去空白并转小写，原始词保留在 severity_raw；②未知严重度既不能丢弃也不能降为信息级，必须归到最高档并标记来源词与 conservative；③CVSS 只接受 CVSS:3.0 与 CVSS:3.1 前缀，八个指标 AV、AC、PR、UI、S、C、I、A 必须各出现一次且值在词表内；④基础分为 AV、AC、PR、UI、S、C、I、A 权重之和，权重 AV 的 N/A/L/P 为 0.85/0.62/0.55/0.2，AC 的 L/H 为 0.77/0.44，PR 的 N/L/H 为 0.85/0.62/0.27，UI 的 N/R 为 0.85/0.62，S 的 U/C 为 0/0.3，C、I、A 的 H/L/N 为 0.56/0.22/0，结果截断到 10 并保留一位小数；⑤向量缺失时基础分留空、reason 记 absent，向量非法时保留原文并标记未解析，不能当成 0 分；⑥路径先剥离尾部 :行号，再统一分隔符，反斜杠、大小写与冗余点段要归一到同一形式；⑦绝对路径报 PATH_ABSOLUTE，越出根目录报 PATH_ESCAPE，空值或非字符串报 PATH_INVALID；⑧记录字段顺序固定为 source、vuln_id、title、weakness、severity、severity_raw、conservative、path、line、cvss_score、cvss_parsed，支持 title/name、path/file、severity/level、weakness/cwe 别名；⑨缺 source、vuln_id、severity、path 时抛 NORMALIZE_MISSING_FIELD，detail 为字段名；⑩批处理逐条接收或拒绝，拒绝项按输入下标记录 code 与原文副本，拒绝不阻塞后续条目，合法项保持输入顺序；⑪limit 限制本次处理条数，未完成批次可 resume，游标与指纹必须一致，否则 BATCH_MISMATCH；⑫完成批次重复提交报 BATCH_ALREADY_COMPLETE，未知批次报 BATCH_UNKNOWN。

硬约束：批处理游标、输出与拒绝项在锁内更新，断点续处理不得重复接收或漏项；接收与拒绝判定必须逐条完成后再写入批次状态，中途异常不得留下半条输出；恢复后的游标、计数、输出顺序必须与中断前一致；记录里的 cvss_score 与 cvss_parsed 必须联动，缺向量时两项分别留空与假；快照深拷贝批处理状态，顶层键为 batches，批次内保留 outputs 与 rejected 列表；恢复非法结构报 SNAPSHOT_INVALID 且原状态不变；同一输入重复归一的结果逐字段一致。

验收：python3 -m pytest -q tests 15 条必须全过；不得修改测试、跳过或按固定输入写死；断点续处理与重启自行验证；只做本地实现，不接外部扫描器。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: scan_result_normalizer
- typeName: ScanResultNormalizer
