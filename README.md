# batch49-py-game-c-12

## 题面

调音台版本更新后，场景里的子混音开始串音：原本只送到环境声总线的雨声，在玩家转过街角后突然从对白总线冒出来；策划把两条总线来回接了两次，同一条路径的增益被叠成双份，整体响度直接爆表。更麻烦的是一段现场配置在热更新时被读到一半，拓扑还没接完，主输出已经把半张路由表算进了增益。范围落在 gsb/batch49-py-game-c-12 的 bus_routing_graph.py，类型 BusRoutingGraph，负责总线的增删、发送连接、环与深度校验、有效增益与延迟计算、静音独奏以及拓扑导出。

公开接口：BusRoutingGraph(*, master_id="master", max_buses=16, max_connections=32, max_depth=4)；add_bus(bus_id, *, gain=1.0, delay_frames=0)；connect(src, dst, *, gain=1.0, delay_frames=0)；disconnect(src, dst)；effective_gain(bus_id)；effective_delay(bus_id)；mute(bus_id, muted=True)；solo(bus_id, enabled=True)；topology_order()；buses()；connections()；snapshot()、restore(state)；stats()；拒绝一律抛 BusError(code, detail)。

要查清的现象与边界：① 构造时主输出总线已经存在，重复建同名总线报 BUS_DUPLICATE，总线数超过 max_buses 报 BUS_LIMIT 且不动已有总线；② 连接两端任一不存在报 UNKNOWN_BUS，自己连自己报 SELF_LOOP，增益为负报 INVALID_CONNECTION；③ 新增连接若让某条路径成环报 CYCLE，路径经过的总线数超过 max_depth 报 DEPTH_LIMIT，被拒的连接不得留下半条边；④ 连接数达到 max_connections 后继续加边报 CONNECTION_LIMIT，已有连接不受影响；⑤ 有效增益按每条源到主输出的路径连乘（含源与沿途各总线的本地增益、每条发送的增益）再对所有并行路径求和；⑥ 有效延迟取增益最大那条路径的延迟之和，增益相同取延迟较小者；⑦ 路径上任意总线被静音，该路径贡献归零，主输出静音则全部归零，取消静音后恢复；⑧ 存在独奏时，只有通往独奏总线的上游路径能出声，与独奏无关的总线归零；独奏沿上游方向筛选，下游节点自身通路不被改写，独奏不反向传播；⑨ 同一对总线重复连接按最后一次的增益与延迟覆盖，不叠加成两条路径；⑩ 断开连接后该路径的延迟补偿归零，重新连接按新路径重新计算而不是沿用旧值，未知连接报 NOT_CONNECTED；⑪ 拓扑顺序必须稳定：每次取入度为零且编号字典序最小的总线，同时就绪时不能靠插入顺序；快照是深拷贝，恢复后总线、连接、静音与独奏标记都在，结构不全报 SNAPSHOT_INVALID 且原图不动。

硬约束：拓扑变更与增益查询并发时整份替换，读侧不得看到半连通图，八路并发建总线加连接到主输出后总线数与增益都完整；总线数、连接数与深度都是硬上限；同一次查询内部不得被并发修改打断。

验收：python3 -m pytest -q tests 十五条全过，不改测试、不跳过、不按固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 音视频处理
- type: Bugfix
- module: bus_routing_graph
- typeName: BusRoutingGraph
