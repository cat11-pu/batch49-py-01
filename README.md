# batch49-py-safety-b-30

## 题面

误报反馈流程这月被打回两次：分析员标了三条误报，系统立刻生成抑制建议，样本只有三条、其中一条还是重复提交的；有人点了应用却没有留操作者，事后追不到人；一条建议撤销后，原先被压掉的命中回到了“新发现”队列，批次号全变了，值班同学又处理了一遍；同一路径第二次反馈时又生成一条一模一样的建议，两条同时生效互相覆盖。复核会上还发现，有几条建议的精确率是拿全仓库命中算的，目录外的扫描结果也被算进分母，建议看起来很美，实际一挂就压掉正常告警。范围是 gsb/batch49-py-safety-b-30 的 suppression_feedback_suggester.py，类型 SuppressionFeedbackSuggester，负责误报样本聚合、建议门禁、二次确认应用与撤销恢复。

公开接口：SuppressionFeedbackSuggester(*, min_samples=3, min_precision=0.8, max_samples=10000)；ingest_hits(hits)；visible_hits()；suppressed_hits()；pattern_for(rule_id, path)；add_sample(sample)；samples_for(pattern)；suggestion(sid)；suggestions()；preview(sid)；prepare_apply(sid)；apply(sid, *, token, operator)；revoke(sid, *, operator, reason)；stats()；snapshot()；restore(state)。命中字段 hit_id、batch_id、rule_id、path、line、observed_at；样本字段 hit_id 与 marked_by。拒绝操作抛 FeedbackError(code, detail)。

故障与边界：①命中必填字段缺失报 FEEDBACK_HIT_INVALID，hit_id 重复报 FEEDBACK_HIT_DUPLICATE，整批原子拒绝；②路径模式由规则标识与目录组成，形如 rule::dir，根目录记作点；③样本只能指向已存在命中，未知命中报 FEEDBACK_SAMPLE_UNKNOWN_HIT，同一命中重复标注报 FEEDBACK_SAMPLE_DUPLICATE，标注人为空报 FEEDBACK_SAMPLE_INVALID；④样本数达到 min_samples 且样本数占该模式命中数的比例达到 min_precision 才生成建议，精确率按四舍五入保留三位；⑤样本不足阈值时只能积累计数，不能提前生成建议；⑥同一模式已有待审或生效建议时，新样本只追加计数与刷新精确率，不生成重复建议；⑦建议状态 pending、active、revoked，创建序号从 1 递增，列表按创建序号排序；⑧应用前必须调用 prepare_apply 取确认令牌，令牌不匹配报 FEEDBACK_CONFIRMATION_INVALID，操作者去空白后为空报 FEEDBACK_OPERATOR_REQUIRED，非 pending 报 FEEDBACK_NOT_PENDING；⑨应用后该模式可见命中全部转为 suppressed 并记下建议标识，重复应用不得二次压缩；⑩撤销只接受 active，缺操作者或原因分别报 FEEDBACK_OPERATOR_REQUIRED 与 FEEDBACK_REASON_REQUIRED，撤销后恢复的命中要按原扫描批次归位，不能算成新命中；⑪快照深拷贝命中、样本、建议与计数器，恢复非法结构报 FEEDBACK_SNAPSHOT_INVALID 且原状态不变；⑫stats 返回 hits、visible、suppressed、samples、suggestions、pending、active、revoked。

硬约束：写入命中、追加样本与应用撤销全程持锁，并发标注不得丢计数或重复生成建议；同一模式在并发下最多一条建议；所有计数以内存中的命中状态为准，不在查询时临时推算；快照恢复后计数、状态与建议序号一致；确认令牌只在成功应用前有效，撤销后不得复用。

验收：python3 -m pytest -q tests 15 条必须全过；不得修改测试、跳过或按固定输入写死；并发与重启自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 漏洞检测与攻防
- type: Bugfix
- module: suppression_feedback_suggester
- typeName: SuppressionFeedbackSuggester
