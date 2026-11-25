# batch49-py-fix-a-02

## 题面

机房割接之后的第二天，出口网关的连接池里混着已吊销的客户端证书，监控却显示这些连接仍在接业务流量；排查时还发现通配 SNI 把两级子域也匹配进了单级规则，请求被打到了不该去的后端。问题落在 gsb/batch49-py-fix-a-02 的 mtls_pool_revocation_router.py，类 MtlsPoolRevocationRouter，用它把 mTLS 连接池的 SNI 路由、证书撤销传播与在途请求迁移做成一套可对账的机制。连接与租约都在本地账本里模拟，不碰真实套接字。

公开入口：MtlsPoolRevocationRouter(*, max_leases_per_conn=4, max_conns_per_host=2)；register_endpoint(host, *, sni, fingerprint, weight=1)；resolve(sni)；open_connection(host, *, conn_id=None)；acquire(sni, *, lease_id=None, now=0)；release(lease_id, *, ok=True)；fail_lease(lease_id, *, reason="")；migrate_lease(lease_id, *, now=0)；apply_revocation(epoch, fingerprints, *, now=0)；rotate_certificate(host, *, fingerprint)；drain_connection(conn_id)；active_leases(sni=None)；snapshot()；restore(state)；stats()。拒绝与失败统一抛 MtlsRoutingError(code, detail)。

要落地的行为：①端点登记要求 host、sni、fingerprint 都非空，weight 为正整数，否则 NET_INVALID_REQUEST；host 重复报 NET_ENDPOINT_EXISTS。SNI 统一去空白、转小写、去掉结尾的点，允许 `*` 与 `*.后缀` 两种通配形态。②resolve 的优先级为：完全相等最高，其次是后缀标签更长的通配，最后才是全局 `*`；同一档内按 weight 降序、host 升序取第一个；查询串本身带通配符报 NET_INVALID_REQUEST，没有任何候选报 NET_SNI_UNKNOWN。③通配只吃一级标签：`*.example.com` 匹配 `a.example.com`，不匹配 `a.b.example.com`。④open_connection 按 host 记录连接，编号形如 c-1，显式编号重复报 NET_CONN_EXISTS，host 未登记报 NET_ENDPOINT_UNKNOWN，每 host 连接数受 max_conns_per_host 限制，连接绑定的指纹取打开那一刻端点当前指纹。⑤acquire 按 resolve 顺序找可用的就绪连接，连接上活跃租约未达 max_leases_per_conn 才算可用；都不行时可在未达连接上限的端点上新建连接；仍拿不到连接报 NET_NO_ROUTE。租约编号形如 l-1，返回的 Lease 带 lease_id、规范化后的 sni、host、conn_id、fingerprint 与 opened_at。⑥release 严格校验，未知或已释放的租约报 NET_LEASE_UNKNOWN；租约所在连接已被吊销时拒绝正常释放，报 NET_LEASE_REVOKED，必须先迁移或调用 fail_lease 显式终止；fail_lease 对吊销连接上的租约同样有效，并计入 failed。⑦apply_revocation 要求 epoch 严格大于当前值，否则 NET_CRL_STALE 并计入 stale_crls；指纹统一小写；命中指纹的连接一律转 revoked，新建连接不得再使用已吊销指纹；返回字典固定含 epoch、revoked、stranded 三个键，stranded 是本次新吊销连接上的活跃租约数。⑧migrate_lease 只处理位于已吊销连接上的租约，其它情况报 NET_NOT_STRANDED；按该租约的 sni 重新走路由顺序挑健康且未排空、有容量的连接，必要时新建；成功时租约号不变，host、conn_id、fingerprint 换成新连接的值，opened_at 取传入的 now，并计入 migrated；挑不到目标报 NET_NO_ROUTE 且租约原样保留。⑨drain_connection 把连接标记为排空，之后不再接新租约，但已有租约仍可正常释放，返回该连接当前活跃租约数；未知连接报 NET_CONN_UNKNOWN。⑩rotate_certificate 只改端点后续新建连接使用的指纹；存量连接保持打开时的指纹，不会因端点换证书被判为已撤销或被自动改绑，已被吊销的旧连接也不会因轮换而复活，旧租约仍需迁移或显式失败。⑪active_leases 按租约号升序返回，可传 sni 过滤；stats 固定返回 endpoints、connections、revoked_connections、active_leases、acquired、released、migrated、failed、stale_crls 九个键。⑫快照深拷贝端点、连接、已吊销指纹集合、CRL 纪元、租约与计数，恢复到新实例后上述状态与统计必须完整复原；非法结构报 NET_SNAPSHOT_INVALID 且原状态不变。

硬约束：register_endpoint、open_connection、acquire、release、fail_lease、migrate_lease、apply_revocation、drain_connection 共用一把可重入锁，并发下同一连接不得被超过上限的租约占用，迁移过程中租约不得同时挂在两条连接上；快照恢复后 CRL 纪元、吊销指纹、连接状态、租约与统计必须一致，且不与快照共享可变对象。

验收：`python3 -m pytest -q tests` 的 15 条必须全过；不得修改测试、跳过或用固定输入写死；并发与恢复自行验证；只做本地实现。

## 运行方式

```bash
python3 -m pytest -q tests
```

## 平台字段

- domain: 安全
- sub: 网络与通信安全
- type: Bugfix
- module: mtls_pool_revocation_router
- typeName: MtlsPoolRevocationRouter
