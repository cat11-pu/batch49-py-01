# batch49-py-game-b-04

## 题面

美术改了一版描边着色器，热重载之后角色黑了半秒，紧接着又恢复成旧版本，看起来像新程序编译到一半就被拿去画了。线上还出现过一次编译失败后整个程序被判成不可用，只能重启客户端。第一批复现的是角色描边和一个后处理全屏 pass，两边的 include 有重叠，改一个公共头文件两个程序都会闪。要改的是 gsb/batch49-py-game-b-04 的 shader_hot_reload_guard.py，类 ShaderHotReloadGuard，负责监听源文件与 include 依赖、标记待重建、编译失败回退和帧边界统一切换。

公开接口：ShaderHotReloadGuard(compile_fn, *, max_rebuilds_per_frame=2, max_dependencies=64)，compile_fn(path) 返回 (是否成功, 错误信息)；watch(program_id, path, include_edges=None) 返回 ProgramInfo；touch_file(path) 返回被标记的程序数；dependencies(program_id) 返回升序 include 闭包；rebuild(program_id) 返回 RebuildOutcome；commit_at_frame_boundary() 返回本次切换的程序数；rollback(program_id) 返回回退后的代；generation(program_id)、program_state(program_id)、pending()、errors()、programs()；snapshot()、restore(state)；stats()。拒绝时抛 ShaderReloadError(code, detail)，码为 SHADER_ 前缀。

要修的点：①程序 id 与源路径不得为空，同 id 重复登记拒绝，compile_fn 必须可调用；②include 图按路径展开成闭包，闭包长度超过 max_dependencies 在登记阶段拒绝；③touch_file 只把闭包含该文件的程序标为待重建并返回标记数，未知文件返回 0 且不改状态；④dependencies 返回升序去重闭包且不含根路径，未知程序报错；⑤只有待重建状态允许 rebuild，其余状态与未知程序分别报错；⑥rebuild 调用 compile_fn，成功后代号不变、状态转为已暂存并进入 pending，失败则保留原代号与可用程序，把错误信息记进 errors；⑦commit 只切换本帧预算内的已暂存程序，按程序 id 升序取前 max_rebuilds_per_frame 个，每个代号加一、状态回到就绪，剩余顺延到下一次；⑧rollback 把单个程序退回上一代，代号减一，已经在第一代时报错；⑨program_state 只返回 ready、dirty、staged、failed 四种；⑩快照覆盖程序表、include 图、代号、暂存队列与计数并深拷贝，恢复后暂存程序仍可提交，非法快照报 SHADER_SNAPSHOT_INVALID 且不改当前状态；⑪stats 给出 programs、dirty、staged、rebuilds、failures、touches、commits、rollbacks；⑫同一文件在一次构建里被多处 include 只重建一次，依赖图出现环必须直接报错，不得递归展开。

补充口径：每帧预算按程序个数计，不按文件个数计；代号从 1 开始，每个程序独立计数；错误信息保留最近一次编译失败的内容，成功编译后清除该程序的旧错误。

硬约束：编译失败后再次 touch 同一文件必须允许重试，失败计数与错误信息保留，旧程序继续可用；切换边界之前提交的绘制命令仍绑定旧程序，切换之后的新命令才拿到新程序；监听、重建与切换会被渲染线程和文件线程同时调用，程序表与暂存队列在锁内整体替换，不允许出现半初始化程序；每帧重建数、依赖闭包长度都是硬上限，超限报错而不是排队溢出。

验收口径：`python3 -m pytest -q tests` 的 15 条用例全部通过，测试不得改动、跳过或按固定输入写死；并发重载、失败重试与重启恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 图形渲染
- type: Bugfix
- module: shader_hot_reload_guard
- typeName: ShaderHotReloadGuard
