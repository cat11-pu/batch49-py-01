import copy
import threading

import pytest

import memquota_tenant_ledger as mql

GB = 1024


def ledger(limit=1000, **kwargs):
    return mql.MemQuotaTenantLedger(global_limit=limit, **kwargs)


def tenant(reg, name, *, quota=1000, floor=0, priority=100):
    reg.add_tenant(name, quota=quota, floor=floor, priority=priority)


def test_add_tenant_validation_and_duplicate():
    reg = ledger()
    bad_calls = [
        dict(name="", quota=100),
        dict(name="   ", quota=100),
        dict(name="a", quota=0),
        dict(name="a", quota=-1),
        dict(name="a", quota=100, floor=-1),
        dict(name="a", quota=100, floor=101),
        dict(name="a", quota=100, priority=-1),
    ]
    for kwargs in bad_calls:
        with pytest.raises(mql.MemQuotaError) as exc:
            reg.add_tenant(**kwargs)
        assert exc.value.code == "MEM_INVALID_REQUEST"

    tenant(reg, "a")
    with pytest.raises(mql.MemQuotaError) as exc:
        tenant(reg, "a")
    assert exc.value.code == "MEM_TENANT_EXISTS"
    assert reg.stats()["tenants"] == 1


def test_charge_and_usage_accounting():
    reg = ledger()
    tenant(reg, "a", quota=1000, floor=100)
    first = reg.charge("a", pid=11, bytes=300)
    second = reg.charge("a", pid=12, bytes=200)

    assert first.charge_id == "chg-1"
    assert first.seq == 1
    assert (first.tenant, first.pid, first.bytes) == ("a", 11, 300)
    assert second.charge_id == "chg-2"
    assert reg.usage() == 500
    assert reg.tenant_usage("a") == 500
    assert [item.charge_id for item in reg.charges()] == ["chg-1", "chg-2"]
    assert [item.charge_id for item in reg.charges("a")] == ["chg-1", "chg-2"]
    assert reg.stats()["usage"] == 500


def test_charge_over_quota_is_rejected_without_partial_state():
    reg = ledger()
    tenant(reg, "a", quota=400)
    reg.charge("a", pid=1, bytes=300)
    with pytest.raises(mql.MemQuotaError) as exc:
        reg.charge("a", pid=2, bytes=200)
    assert exc.value.code == "MEM_QUOTA_EXCEEDED"
    assert exc.value.detail == {"tenant": "a", "used": 300,
                                "requested": 200, "quota": 400}
    assert reg.usage() == 300
    assert len(reg.charges("a")) == 1


def test_release_returns_bytes_and_rejects_second_release():
    reg = ledger()
    tenant(reg, "a")
    charge = reg.charge("a", pid=1, bytes=300)
    assert reg.release(charge.charge_id) == 300
    assert reg.usage() == 0
    assert reg.tenant_usage("a") == 0
    assert reg.charges("a") == ()
    with pytest.raises(mql.MemQuotaError) as exc:
        reg.release(charge.charge_id)
    assert exc.value.code == "MEM_CHARGE_UNKNOWN"


def test_enforce_under_limit_is_a_noop():
    reg = ledger(limit=1000)
    tenant(reg, "a")
    reg.charge("a", pid=1, bytes=400)
    report = reg.enforce()
    assert report.reclaimed == ()
    assert report.oom is None
    assert report.usage_before == 400
    assert report.usage_after == 400
    assert report.unresolved is False
    assert reg.stats()["enforce_calls"] == 1


def test_enforce_reclaims_from_least_protected_tenant_first():
    reg = ledger(limit=900)
    tenant(reg, "a", quota=2000, priority=10)
    tenant(reg, "b", quota=2000, priority=90)
    tenant(reg, "c", quota=2000, priority=50)
    reg.charge("a", pid=1, bytes=400)
    reg.charge("b", pid=2, bytes=400)
    reg.charge("c", pid=3, bytes=400)

    report = reg.enforce()
    assert report.reclaimed == ("chg-2",)
    assert report.usage_before == 1200
    assert report.usage_after == 800
    assert report.oom is None
    assert report.unresolved is False
    assert reg.tenant_usage("b") == 0
    assert reg.stats()["reclaimed_charges"] == 1


def test_enforce_selects_oom_when_guaranteed_floors_block_reclaim():
    reg = ledger(limit=600)
    tenant(reg, "a", quota=500, floor=500, priority=10)
    tenant(reg, "b", quota=500, floor=500, priority=90)
    reg.charge("a", pid=1, bytes=500)
    reg.charge("b", pid=2, bytes=500)

    report = reg.enforce()
    assert report.reclaimed == ()
    assert report.unresolved is False
    assert report.oom is not None
    assert report.oom.tenant == "b"
    assert report.oom.charge_id == "chg-2"
    assert report.oom.pid == 2
    assert report.oom.bytes == 500
    assert report.oom.priority == 90
    assert report.oom.reason == "MEM_OOM_OVER_LIMIT"
    assert reg.stats()["oom_selected"] == 1


def test_oom_candidates_are_ordered_by_priority_and_capacity():
    reg = ledger(limit=1000)
    tenant(reg, "a", quota=2000, priority=10)
    tenant(reg, "b", quota=2000, priority=30)
    tenant(reg, "c", quota=2000, priority=90)
    reg.charge("a", pid=1, bytes=800)
    reg.charge("b", pid=2, bytes=600)
    reg.charge("c", pid=3, bytes=400)

    assert [(item.tenant, item.bytes) for item in reg.oom_candidates()] == [
        ("c", 400), ("b", 600), ("a", 800)]
    assert [(item.tenant, item.bytes) for item in reg.oom_candidates(limit=2)] == [
        ("c", 400), ("b", 600)]
    with pytest.raises(mql.MemQuotaError) as exc:
        reg.oom_candidates(limit=0)
    assert exc.value.code == "MEM_INVALID_REQUEST"


def test_remove_tenant_requires_zero_usage():
    reg = ledger()
    tenant(reg, "a")
    charge = reg.charge("a", pid=1, bytes=100)
    with pytest.raises(mql.MemQuotaError) as exc:
        reg.remove_tenant("a")
    assert exc.value.code == "MEM_TENANT_BUSY"
    reg.release(charge.charge_id)
    assert reg.remove_tenant("a") == 0
    assert reg.stats()["tenants"] == 0
    with pytest.raises(mql.MemQuotaError) as exc:
        reg.tenant_usage("a")
    assert exc.value.code == "MEM_TENANT_UNKNOWN"


def test_tenant_capacity_limit():
    reg = ledger(max_tenants=2)
    tenant(reg, "a")
    tenant(reg, "b")
    with pytest.raises(mql.MemQuotaError) as exc:
        tenant(reg, "c")
    assert exc.value.code == "MEM_CAPACITY_EXCEEDED"
    assert reg.stats()["tenants"] == 2


def test_charge_ids_are_global_and_listing_is_ordered():
    reg = ledger(limit=4000)
    tenant(reg, "a", quota=2000)
    tenant(reg, "b", quota=2000)
    reg.charge("b", pid=1, bytes=100)
    reg.charge("a", pid=2, bytes=200)
    reg.charge("b", pid=3, bytes=300)
    assert [item.charge_id for item in reg.charges()] == [
        "chg-1", "chg-2", "chg-3"]
    assert [item.seq for item in reg.charges("b")] == [1, 3]
    assert reg.usage() == 600


def test_resize_global_limit_validates_and_updates():
    reg = ledger(limit=1000)
    for bad in (0, -1, 2.5):
        with pytest.raises(mql.MemQuotaError) as exc:
            reg.resize_global_limit(bad)
        assert exc.value.code == "MEM_INVALID_REQUEST"
    reg.resize_global_limit(2048)
    assert reg.stats()["global_limit"] == 2048


def test_snapshot_restore_roundtrip_is_deep():
    reg = ledger(limit=900)
    tenant(reg, "a", quota=800, floor=100, priority=10)
    tenant(reg, "b", quota=800, priority=90)
    reg.charge("a", pid=1, bytes=300)
    reg.charge("b", pid=2, bytes=200)
    state = reg.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "global_limit", "tenants", "charges",
                             "counters", "stats"}

    reg.charge("a", pid=3, bytes=100)
    state["charges"].clear()
    state["stats"]["usage"] = 99
    assert reg.stats()["usage"] == 600

    clone = ledger(limit=10)
    clone.restore_state(pristine)
    assert clone.stats()["usage"] == 500
    assert clone.stats()["global_limit"] == 900
    assert [item.charge_id for item in clone.charges()] == ["chg-1", "chg-2"]
    assert clone.tenant_usage("a") == 300
    clone.release("chg-2")
    assert clone.usage() == 300


def test_restore_state_rejects_malformed_payload():
    reg = ledger()
    tenant(reg, "a")
    reg.charge("a", pid=1, bytes=250)
    bad_states = [
        None,
        {},
        {"version": 2, "global_limit": 1000, "tenants": {}, "charges": [],
         "counters": {}, "stats": {}},
        {"version": 1, "global_limit": 1000, "tenants": [], "charges": [],
         "counters": {}, "stats": {}},
        {"version": 1, "global_limit": 1000, "tenants": {}, "charges": "bad",
         "counters": {}, "stats": {}},
        {"version": 1, "global_limit": 0, "tenants": {}, "charges": [],
         "counters": {}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(mql.MemQuotaError) as exc:
            reg.restore_state(bad)
        assert exc.value.code == "MEM_SNAPSHOT_INVALID"
    assert reg.usage() == 250


def test_concurrent_charge_respects_quota_and_unique_ids():
    reg = ledger()
    tenant(reg, "a", quota=400)
    issued = []
    failures = []
    barrier = threading.Barrier(10)

    def worker():
        try:
            barrier.wait(timeout=5)
            issued.append(reg.charge("a", pid=1, bytes=100).charge_id)
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(issued) == 4
    assert len(set(issued)) == 4
    assert sorted(getattr(exc, "code", repr(exc)) for exc in failures) == [
        "MEM_QUOTA_EXCEEDED"] * 6
    assert reg.usage() == 400
    assert len(reg.charges("a")) == 4
