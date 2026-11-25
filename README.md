# batch49-py-safety-b-08

## 题面

基础镜像团队这月两次被 SBOM 转换坑到：第一次是把 SPDX 清单转成 CycloneDX 时，包里那条只在上游公告里出现的许可证备注被悄悄丢掉，事后审计追问没人说得清；第二次是把容器里的 CycloneDX 文档转回 SPDX，组件顺序被按名字重排，依赖链指向了错误的坐标；还有一次是同一个包同时标了两种许可证，转换脚本按 AND 拆成两条，客户比对不上原始声明。审计要求每次转换都留下可复现的记录，出问题能按字段回溯到原文档。请在 gsb/batch49-py-safety-b-08/sbom_format_bridge.py 中实现 SbomFormatBridge，用 dataclass 描述中间模型，在两种文档结构之间转换组件、依赖关系、许可证与外部引用，不能一一对应的字段写进附加属性并在转换报告里列明。

构造函数为 SbomFormatBridge(*, max_components=500)。公开接口：parse_spdx(payload) -> SbomDoc、parse_cyclonedx(payload) -> SbomDoc、to_spdx(doc) -> dict、to_cyclonedx(doc) -> dict、convert(doc, target) -> tuple、roundtrip(payload, source_format) -> dict、validate(payload, format) -> list、target_formats() -> list、component_index(doc) -> dict、summary(doc) -> dict、supported_relationship_kinds() -> list、to_json(payload) -> str。中间模型含 Component（name、version、supplier、license_expression、external_refs、extra）、Relationship（source、target、kind）、SbomDoc（format、name、components、relationships、document_properties）与 ConversionReport（source_format、target_format、preserved、warnings），SbomBridgeError 带 code 与 detail。

故障与边界：
①SPDX 侧组件取 packages，按文档次序保序，SPDXID 用于把 relationships 解析成组件名，缺失 supplier 记 None，SPDXID 与 bom-ref 在往返中重新生成但保持稳定。
②SPDX 包的 externalRefs 按 referenceType 收进 external_refs，未映射字段（如 licenseComments）进 extra，extra 的键保持原字段名，不翻译、不合并。
③CycloneDX 侧组件取 components，供应商来自 supplier.name，许可证取 licenses 数组里第一条单一 id，多条许可证不做拆分，文档级 properties 收进 document_properties，文档级属性不参与组件比对。
④CycloneDX 的 dependencies 每条 ref 与 dependsOn 组合成 DEPENDS_ON 关系。
⑤validate 逐字段返回路径列表，顶层字段用字段名、数组元素用 packages[序号].字段名 的形式，结果按字典序排序，SPDX 必填 spdxVersion、name、packages，每个包必填 name、versionInfo、SPDXID；CycloneDX 必填 bomFormat、metadata.component.name、components，每个组件必填 name、version、bom-ref。
⑥格式不符时抛 DOCUMENT_FORMAT_MISMATCH，detail 为缺失的判别字段名，SPDX 侧是 spdxVersion，CycloneDX 侧是 bomFormat。
⑦转换后组件次序按原文档次序，report 的 preserved 用 components[序号].字段名 标注被搬进附加属性的字段，warnings 只记录确实无法保留的字段，本次转换不应出现丢弃，附加属性在 CycloneDX 侧统一加 gsb: 前缀。
⑧目标格式无法表达的关系类型必须走附加属性保留，不得静默丢弃；多许可证表达式在目标格式不支持 AND/OR 混写时整串保留原文，不得拆散改写。
⑨往返转换后语义等价的部分必须完全一致，组件顺序要按原文档次序恢复，不能按组件名重排；roundtrip 依次完成解析、转到另一格式、再解析、转回源格式，返回源格式文档。
⑩转换报告要能按字段回放，同一文档重复转换结果稳定且不得修改传入文档，to_json 输出按键排序的稳定文本。
⑪组件数超过 max_components 时抛 COMPONENT_LIMIT_EXCEEDED 且不得留下部分转换结果；未知目标格式抛 FORMAT_UNSUPPORTED；文档级属性在两种格式之间往返后保持原值。
⑫component_index 返回组件名到首次出现下标的映射，重名只登记第一次，summary 给出格式、名称、组件数与关系数。

验收：python3 -m pytest -q tests 15 条必须全部通过；不得修改测试、不得跳过、不得按固定输入写死；只做本地实现，不联网，不引第三方库。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: sbom_format_bridge
- typeName: SbomFormatBridge
- file: sbom_format_bridge.py
