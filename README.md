# batch49-py-safety-a-28

## 题面

值班同事巡检宿主机时发现告警要么整夜不响，要么同一挂载点反复刷屏：运维在容器里 bind 了 /srv，宿主侧基线被整轮覆盖，之后的巡检全按被覆盖后的表比较，真正的异常再也报不出来；两次扫描之间短暂挂载又卸载的痕迹完全看不到，事后只能靠人工回忆；同一路径在一轮扫描里被覆盖两次，报告只留下最终状态，中间那层被隐藏的目录永远查不到。更糟的是有人手工改了基线，扫描结果与告警对不上，排障时无法判断哪一轮是可信的。范围是 gsb/batch49-py-safety-a-28 的 overmount_drift_detector.py，类 OvermountDriftDetector，周期读取挂载表并按 mount id 与 parent id 重建树，与基线比对。

公开接口：OvermountDriftDetector(max_entries=32, alert_window=2.0, clock=None)；baseline(rows) 记录基线并返回条目数与版本；mount_tree(rows=None) 返回按 mount id 建的树；scan(rows) 返回 entries、orphans、truncated；diff(current=None) 返回各类漂移；confirm() 提交最近扫描；recover(target) 标记已处理漂移；alerts() 返回告警；clear_alerts() 清空告警；snapshot()/restore(state) 保存恢复；stats() 返回计数。拒绝统一抛 DriftError(code, detail)。挂载行字段固定为 mount_id、parent_id、path、device、fs_type、mounted_at、namespace。

故障与边界：①baseline 记录每个挂载点的 mount id、设备、文件系统类型、挂载时间与父 id，只有 baseline 与 confirm 能改写基线，scan 永远只读；②scan 按 mount_id 与 parent_id 重建树，父节点必须在同一命名空间内，找不到父的条目单列进 orphans 并带 missing_parent，不能混进正常条目；③mount id 重复、父子成环或同命名空间同一路径重复的挂载表整轮丢弃，抛 TABLE_INCONSISTENT，基线、告警与上一轮扫描结果都不变；④当前有新路径而基线没有的标 new；⑤新挂载占用基线已有路径的标 overmount 并给出 covered_path；⑥基线有而当前消失的标 vanished，不自动删基线，下次扫描继续报；⑦mount id 相同但 mounted_at 不同的标 reused；⑧同一路径同一状态在告警窗口内只产生一条告警，窗口按单调时钟计算，超出窗口才允许再报；⑨confirm 把最近一次扫描结果整体写为新基线并返回条目数，之后同一张表扫描为 unchanged；⑩单轮条目数超过 max_entries 按稳定顺序截断并给出 truncated 数量，不能只截断不计数；⑪宿主与容器命名空间分开比较，不跨树配对；⑫recover 只接受最近一轮确实存在的漂移目标，第二次调用抛 NOT_DRIFTED；快照深拷贝，restore 非法抛 STATE_INVALID 且状态不变；stats 返回 scans、alerts、confirmed、recovered、rejected。

还有三条容易漏：同一挂载点被 bind 到自己时路径不变但挂载树已变，必须按 mount id 识别而不能只比路径；两次扫描之间新增又删除的挂载在稳态扫描里看不到，要结合内核事件或变更计数；同一挂载点在一轮扫描内被覆盖两次，diff 要把两次覆盖都报出而不是只保留最终状态。

硬约束：scan 不得写基线，告警去重窗口按单调时钟；条目数与单轮耗时有上限，超限分批或截断但不能丢计数；快照恢复后基线、告警去重状态与统计一致，恢复失败不得改动现场。验收：python3 -m pytest -q tests 15 条全过；不得改测试、跳过或按固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 系统安全
- type: Bugfix
- module: overmount_drift_detector
- typeName: OvermountDriftDetector
