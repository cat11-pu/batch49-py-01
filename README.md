# batch49-py-fix-a-09

## 题面

一次补丁回滚演练暴露了流程里最薄的两处：回滚包在传递途中被改掉其中一批的资产版本，执行端没验签，照样把生产库退回两级；一批资产回滚失败后脚本直接退出，后面本可继续的批次全被卡住；值班手工补时又把已退回的资产再退一遍，版本号来回跳。调度进程重启后，哪些资产退过、哪些批次失败全丢了，同一份请求重放还会重新执行。现在要把回滚入口收成一个带签名与批次隔离的执行器，落在 gsb/batch49-py-fix-a-09 的 rollback_bundle_guard.py，类 RollbackBundleGuard。

公开入口：RollbackBundleGuard(undo, *, max_batches=8, max_assets_per_batch=32, max_attempts=2)；trust_key(key_id, material)；untrust_key(key_id)；verify_batch(batch)；submit(bundle, *, request_id, now=0)；execute(request_id, *, now=0)；retry_batch(request_id, batch_id, *, now=0)；bundle_status(request_id)；history(request_id=None)；snapshot()；restore(state)；stats()。undo 以 (asset_id, from_version, to_version) 调用，只有返回 True 才算成功。拒绝统一抛 RollbackError(code, detail)。

要落地的行为：①bundle 是带 bundle_id 与 batches 的字典，batches 非空且不超过 max_batches；每个 batch 含 batch_id、key_id、assets 与 signature，assets 非空且不超过 max_assets_per_batch，批内 asset_id 不得重复，from_version 与 to_version 必须为整数且不能相等。结构不合法、批号重复或超限一律抛 ROLLBACK_BUNDLE_INVALID，且不留下任何请求记录。②签名正文是 {"batch_id":..., "assets":[资产按原顺序排列]} 的紧凑排序 JSON，signature 为用 key_id 对应材料做的 HMAC-SHA256 小写十六进制；资产顺序参与签名，重排后即使集合相同也必须判签名失效。③verify_batch 返回 batch_id、status、code、detail：结构坏记 ROLLBACK_BATCH_INVALID，key_id 未登记记 ROLLBACK_KEY_UNKNOWN，摘要不符记 ROLLBACK_SIGNATURE_INVALID，通过时 status 为 ok。④submit 先整包校验，再逐批验签；验签失败的批次只把自己标成 quarantined，不影响其它批次进入待执行，返回值含 accepted 数量、quarantined 列表与 total_batches。⑤同一资产在后面的批次再次出现时，后一批整批隔离并记 ROLLBACK_ASSET_DUPLICATE；该资产若已在别的请求里以同一个 from_version 成功回滚过，后一批记 ROLLBACK_ASSET_SETTLED，但 from_version 已前进时应允许再次回滚。⑥execute 按批次声明顺序、批内资产顺序调用 undo，成功就把资产记入已完成并推进全局结算台账；返回或抛异常都算该批失败，失败批之前的资产保持已退状态，后面的批次照常执行，批次之间不得互相回滚。⑦执行结果 status 取 completed、partial 或 failed：可信批全完成是 completed，有隔离批或部分批失败是 partial，已执行批全失败是 failed；done、failed、quarantined、undone 分别按批号、失败资产与执行顺序列出。⑧同一 request_id 重复 submit 完全相同的包时返回首次结果且不重复登记，换了包内容抛 ROLLBACK_REQUEST_CONFLICT；同一请求重复 execute 直接返回首次结果，undo 不得再被调用，未知请求抛 ROLLBACK_REQUEST_UNKNOWN。⑨retry_batch 只接受 failed 批次，从失败资产继续，已完成资产不得再调 undo；重试前重新验签，密钥被撤销导致验签失败时该批转 quarantined 并抛 ROLLBACK_BATCH_QUARANTINED；attempts 达到 max_attempts 抛 ROLLBACK_ATTEMPTS_EXCEEDED，重试 done 批抛 ROLLBACK_BATCH_NOT_FAILED，批号不存在抛 ROLLBACK_BATCH_UNKNOWN。⑩submit 时密钥未登记、或重试前密钥被 untrust_key 撤销，都必须只隔离相关批次，不影响其它批次执行与已完成台账。⑪bundle_status 返回请求级状态、已退资产与每批的 status、code、attempts、done；history 按发生顺序记录 submit、undo、done、fail 事件并可按请求过滤；stats 至少给出 requests、batches、executed、undone、retries、quarantined。⑫snapshot 返回深拷贝，含信任密钥、请求台账、全局结算表、事件与计数；restore 结构不符抛 ROLLBACK_SNAPSHOT_INVALID 且原状态可用，恢复后重放同一请求不得再次调用 undo，失败批仍可继续重试。

硬约束：同一 request_id 的 submit 与 execute 在一把可重入锁内串行，多个执行方并发时每个资产最多调用一次 undo，结果对所有调用方一致；批次隔离意味着前一批失败不能回滚后一批已完成的工作，也不能阻止后一批开始。快照与恢复整体替换台账与结算表，恢复失败不能留下半套状态。

验收：`python3 -m pytest -q tests` 的 15 条全部通过；不得修改测试、跳过或用固定输入写死；并发与快照恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: rollback_bundle_guard
- typeName: RollbackBundleGuard
