import copy
import threading

import pytest

import mtls_pool_revocation_router as mpr


def router(**kwargs):
    return mpr.MtlsPoolRevocationRouter(**kwargs)


def endpoint(reg, host, *, sni="api.example.com", fingerprint="fp-a", weight=1):
    reg.register_endpoint(host, sni=sni, fingerprint=fingerprint, weight=weight)


def test_register_normalizes_sni_and_rejects_bad_endpoints():
    reg = router()
    reg.register_endpoint("api-1", sni=" API.Example.COM. ",
                          fingerprint="FP-A", weight=2)
    assert reg.resolve("api.example.com") == "api-1"
    assert reg.resolve("API.EXAMPLE.COM.") == "api-1"

    bad_calls = [
        dict(host="", sni="api.example.com", fingerprint="fp"),
        dict(host="h1", sni="   ", fingerprint="fp"),
        dict(host="h2", sni="api.example.com", fingerprint="  "),
        dict(host="h3", sni="api.example.com", fingerprint="fp", weight=0),
    ]
    for kwargs in bad_calls:
        with pytest.raises(mpr.MtlsRoutingError) as exc:
            reg.register_endpoint(**kwargs)
        assert exc.value.code == "NET_INVALID_REQUEST"


def test_resolve_prefers_exact_over_wildcard():
    reg = router()
    endpoint(reg, "global", sni="*", fingerprint="fp-g", weight=9)
    endpoint(reg, "wild", sni="*.example.com", fingerprint="fp-w", weight=9)
    endpoint(reg, "exact", sni="api.example.com", fingerprint="fp-e")
    assert reg.resolve("api.example.com") == "exact"
    assert reg.resolve("web.example.com") == "wild"
    assert reg.resolve("other.org") == "global"


def test_resolve_prefers_longest_wildcard_then_weight_then_host():
    reg = router()
    endpoint(reg, "short", sni="*.example.com", fingerprint="fp-1", weight=9)
    endpoint(reg, "long", sni="*.api.example.com", fingerprint="fp-2")
    assert reg.resolve("a.api.example.com") == "long"
    assert reg.resolve("a.example.com") == "short"

    endpoint(reg, "b-tie", sni="*.other.com", fingerprint="fp-3", weight=2)
    endpoint(reg, "a-tie", sni="*.other.com", fingerprint="fp-4", weight=2)
    assert reg.resolve("x.other.com") == "a-tie"

    endpoint(reg, "a-light", sni="*.third.com", fingerprint="fp-5", weight=1)
    endpoint(reg, "z-heavy", sni="*.third.com", fingerprint="fp-6", weight=8)
    assert reg.resolve("x.third.com") == "z-heavy"


def test_wildcard_matches_single_label_and_unknown_sni_is_refused():
    reg = router()
    endpoint(reg, "wild", sni="*.example.com")
    assert reg.resolve("a.example.com") == "wild"
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.resolve("a.b.example.com")
    assert exc.value.code == "NET_SNI_UNKNOWN"
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.resolve("*.example.com")
    assert exc.value.code == "NET_INVALID_REQUEST"


def test_acquire_opens_one_connection_and_returns_lease_view():
    reg = router()
    endpoint(reg, "api-1", fingerprint="fp-a")
    lease = reg.acquire("API.example.com.", now=100)

    assert lease.lease_id == "l-1"
    assert lease.sni == "api.example.com"
    assert lease.host == "api-1"
    assert lease.conn_id == "c-1"
    assert lease.fingerprint == "fp-a"
    assert lease.opened_at == 100
    assert reg.active_leases() == (lease,)
    assert reg.stats()["acquired"] == 1
    assert reg.stats()["connections"] == 1


def test_acquire_opens_second_connection_when_leases_full():
    reg = router(max_leases_per_conn=1, max_conns_per_host=2)
    endpoint(reg, "api-1")
    first = reg.acquire("api.example.com")
    second = reg.acquire("api.example.com")
    assert (first.conn_id, second.conn_id) == ("c-1", "c-2")
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.acquire("api.example.com")
    assert exc.value.code == "NET_NO_ROUTE"
    assert len(reg.active_leases()) == 2
    assert reg.stats()["connections"] == 2


def test_release_is_strict_and_frees_capacity_for_reuse():
    reg = router(max_leases_per_conn=1)
    endpoint(reg, "api-1")
    first = reg.acquire("api.example.com")
    assert reg.release(first.lease_id) == "c-1"
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.release(first.lease_id)
    assert exc.value.code == "NET_LEASE_UNKNOWN"

    second = reg.acquire("api.example.com")
    assert second.conn_id == "c-1"
    assert reg.stats()["released"] == 1


def test_revocation_marks_connections_reports_stranded_and_epoch_is_monotonic():
    reg = router()
    endpoint(reg, "api-1", fingerprint="fp-a")
    reg.acquire("api.example.com")
    reg.acquire("api.example.com")

    report = reg.apply_revocation(3, ["FP-A"])
    assert report == {"epoch": 3, "revoked": 1, "stranded": 2}
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.acquire("api.example.com")
    assert exc.value.code == "NET_NO_ROUTE"
    assert reg.stats()["revoked_connections"] == 1

    for stale in (3, 2):
        with pytest.raises(mpr.MtlsRoutingError) as exc:
            reg.apply_revocation(stale, [])
        assert exc.value.code == "NET_CRL_STALE"
    assert reg.stats()["stale_crls"] == 2


def test_acquire_falls_over_to_endpoint_with_live_certificate():
    reg = router()
    endpoint(reg, "api-a", fingerprint="fp-a", weight=5)
    endpoint(reg, "api-b", fingerprint="fp-b", weight=1)
    first = reg.acquire("api.example.com")
    assert first.host == "api-a"

    reg.apply_revocation(1, ["fp-a"])
    second = reg.acquire("api.example.com")
    assert second.host == "api-b"
    assert second.fingerprint == "fp-b"
    assert second.conn_id == "c-2"


def test_stranded_lease_cannot_be_released_but_can_be_failed():
    reg = router()
    endpoint(reg, "api-1", fingerprint="fp-a")
    lease = reg.acquire("api.example.com")
    reg.apply_revocation(1, ["fp-a"])

    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.release(lease.lease_id)
    assert exc.value.code == "NET_LEASE_REVOKED"
    assert reg.active_leases() == (lease,)

    assert reg.fail_lease(lease.lease_id, reason="crl") == "c-1"
    assert reg.active_leases() == ()
    assert reg.stats()["failed"] == 1
    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.fail_lease(lease.lease_id)
    assert exc.value.code == "NET_LEASE_UNKNOWN"


def test_migrate_moves_lease_and_preserves_identity():
    reg = router()
    endpoint(reg, "api-a", fingerprint="fp-a")
    endpoint(reg, "api-b", fingerprint="fp-b")
    lease = reg.acquire("api.example.com")
    reg.apply_revocation(1, ["fp-a"])

    moved = reg.migrate_lease(lease.lease_id, now=50)
    assert moved.lease_id == lease.lease_id
    assert moved.host == "api-b"
    assert moved.conn_id == "c-2"
    assert moved.fingerprint == "fp-b"
    assert moved.opened_at == 50
    assert reg.release(moved.lease_id) == "c-2"
    assert reg.stats()["migrated"] == 1


def test_migrate_without_capacity_keeps_lease_in_place():
    reg = router(max_conns_per_host=1)
    endpoint(reg, "api-1", fingerprint="fp-a")
    lease = reg.acquire("api.example.com")
    reg.apply_revocation(1, ["fp-a"])

    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.migrate_lease(lease.lease_id)
    assert exc.value.code == "NET_NO_ROUTE"
    assert reg.active_leases() == (lease,)
    assert reg.stats()["migrated"] == 0
    assert reg.fail_lease(lease.lease_id) == "c-1"


def test_drain_blocks_new_leases_but_not_release():
    reg = router(max_conns_per_host=2)
    endpoint(reg, "api-1", fingerprint="fp-a")
    lease = reg.acquire("api.example.com")
    assert reg.drain_connection(lease.conn_id) == 1

    fresh = reg.acquire("api.example.com")
    assert fresh.conn_id == "c-2"
    assert reg.release(lease.lease_id) == "c-1"

    with pytest.raises(mpr.MtlsRoutingError) as exc:
        reg.drain_connection("c-9")
    assert exc.value.code == "NET_CONN_UNKNOWN"


def test_concurrent_acquire_does_not_oversubscribe_connection():
    reg = router(max_leases_per_conn=2, max_conns_per_host=1)
    endpoint(reg, "api-1")
    issued = []
    failures = []
    barrier = threading.Barrier(10)

    def worker():
        try:
            barrier.wait(timeout=5)
            issued.append(reg.acquire("api.example.com").lease_id)
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(issued) == ["l-1", "l-2"]
    assert sorted(getattr(exc, "code", repr(exc)) for exc in failures) == [
        "NET_NO_ROUTE"] * 8
    assert reg.stats()["acquired"] == 2
    assert len(reg.active_leases()) == 2


def test_snapshot_restore_roundtrip_and_invalid_state():
    reg = router()
    endpoint(reg, "api-a", fingerprint="fp-a")
    endpoint(reg, "api-b", fingerprint="fp-b")
    lease = reg.acquire("api.example.com")
    reg.apply_revocation(2, ["fp-a"])
    state = reg.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "endpoints", "connections",
                             "revoked_fingerprints", "crl_epoch", "leases",
                             "counters", "stats"}

    reg.acquire("api.example.com")
    state["leases"].clear()
    state["stats"]["acquired"] = 99
    assert reg.stats()["acquired"] == 2

    clone = router()
    clone.restore(pristine)
    assert clone.stats()["acquired"] == 1
    assert clone.stats()["revoked_connections"] == 1
    assert [item.lease_id for item in clone.active_leases()] == [lease.lease_id]
    assert clone.migrate_lease(lease.lease_id).conn_id == "c-2"

    for bad in (None, {}, {"version": 2}, {"version": 1, "endpoints": []},
                {"version": 1, "endpoints": {}, "connections": "bad",
                 "revoked_fingerprints": [], "crl_epoch": 0, "leases": [],
                 "counters": {}, "stats": {}}):
        with pytest.raises(mpr.MtlsRoutingError) as exc:
            clone.restore(bad)
        assert exc.value.code == "NET_SNAPSHOT_INVALID"
    assert clone.stats()["acquired"] == 1
