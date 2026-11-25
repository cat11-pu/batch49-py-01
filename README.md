# batch49-py-game-a-09

## 题面

固定步长战斗复现困难：两只怪贴墙时只结算了一个接触点，慢速滚下斜坡的箱子一直抖动不肯休眠，恢复系数填成 1.4 的精英怪撞墙后反向飞出去，读档回到前一天的位置还会带着上一帧的穿透，同一个战斗录像在两台机器上跑出的位置也对不上。范围是 gsb/batch49-py-game-a-09 的 contact_impulse_resolver.py，类 ContactImpulseResolver，负责 2D 圆形与轴对齐盒体的接触检测、冲量解算与穿透修正。

公开接口：ContactImpulseResolver(position_correction=0.8, max_correction=0.25, sleep_speed=0.02, iterations=4)；add_body(body)、remove_body(body_id)、set_material(body_id, restitution, friction)、step(dt, iterations=None)、contacts()、body(body_id)、wake(body_id)、sleep_body(body_id)、sleeping(body_id)、apply_impulse(body_id, ix, iy)、stats()、snapshot()、restore(state)。刚体与接触记录用 RigidBody、Contact 两个 dataclass 表示，字段包括标识、形状、位置、速度、半径、半宽半高、质量、恢复系数、摩擦系数、静态标记与休眠标记。拒绝操作抛 ContactError(code, detail)。Contact 含 a、b、nx、ny、penetration 与 impulse，stats 返回 bodies、contacts、sleeping 三个计数。

故障与边界：①同一标识重复加入报 DUPLICATE_BODY，形状、尺寸或质量非法报 INVALID_BODY；②接触对按标识升序规范化，contacts 按 (a,b) 排序，每帧 step 后整体替换上一帧结果；③圆与圆、圆与盒、盒与盒的重叠深度与法线方向要正确，分离的刚体不得产生接触；④法线方向速度接近时按恢复系数与质量倒数计算冲量，静止体质量视为无穷大；⑤位置修正按比例消除穿透，单帧修正量不得超过 max_correction；⑥低速刚体可进入休眠，休眠体不参与积分，显式唤醒后才恢复；⑦apply_impulse 按质量换算速度并唤醒目标；⑧set_material 保存恢复系数与摩擦系数，供后续每帧解算使用；⑨同一对刚体在一帧内出现多个接触点时只生成一条接触记录，冲量合并；⑩恢复系数超出合法区间时按边界值钳制，不得报错或反向弹射；⑪休眠体被新接触唤醒时当帧只唤醒不参与解算，下一帧才受力；⑫快照结构不合法报 INVALID_SNAPSHOT，恢复后刚体位置、速度、材质与休眠标记都要还原。

硬约束：固定步长与固定迭代次数，同一初始状态连续跑两遍的轨迹必须逐位一致，不得依赖字典遍历顺序；单帧接触记录数量与内存分配受实体数约束，检测阶段不得为每对刚体新建持久对象；快照恢复后接触缓存清空，读方不得看到恢复前的半帧接触。静态刚体只作为解算对照，不得被位置修正推动，也不参与休眠判定。

验收：python3 -m pytest -q tests 的 15 条用例全部通过，不得改测试、跳过或按固定输入写死；休眠唤醒与快照恢复自行验证；只做本地实现，所有接口只依赖标准库。交付源码、测试输出与一页以内的解算口径说明，验收以仓库内测试文件为准。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 游戏、图形与多媒体
- sub: 游戏开发
- type: Bugfix
- module: contact_impulse_resolver
- typeName: ContactImpulseResolver
