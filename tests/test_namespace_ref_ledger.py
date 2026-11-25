import pytest

import namespace_ref_ledger as nrl


def ledger(**kwargs):
    return nrl.NamespaceRefLedger(**kwargs)


def holder(pid, starttime, mode="fd"):
    return {"pid": pid, "starttime": starttime, "mode": mode}


def test_track_records_holders_by_inode():
    led = ledger()
    out = led.track(11, 100, starttime=1)
    assert out == {"inode": 11, "pid": 100, "starttime": 1,
                   "holders": 1, "paths": []}
    led.track(11, 101, starttime=2)
    assert led.refcount(11) == 2
    assert led.holders(11) == [holder(100, 1), holder(101, 2)]
    assert led.state(11) == "active"


def test_pid_reuse_is_split_by_starttime():
    led = ledger()
    led.track(11, 7, starttime=100)
    led.track(11, 7, starttime=200)
    assert led.refcount(11) == 2
    assert [item["starttime"] for item in led.holders(11)] == [100, 200]


def test_retracking_same_holder_updates_mode_and_paths():
    led = ledger()
    led.track(11, 7, starttime=1, path="/proc/7/ns/net")
    led.track(11, 7, starttime=1, mode="bind", path="/proc/8/ns/net")
    assert led.refcount(11) == 1
    assert led.holders(11) == [holder(7, 1, "bind")]
    assert led.paths(11) == ["/proc/7/ns/net", "/proc/8/ns/net"]


def test_release_one_holder_keeps_namespace_active():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.track(11, 101, starttime=2)
    assert led.release(11, 100, starttime=1) == 1
    assert led.refcount(11) == 1
    assert led.state(11) == "active"
    assert [item["pid"] for item in led.holders(11)] == [101]


def test_release_last_holder_marks_reclaimable():
    led = ledger()
    led.track(11, 100, starttime=1)
    assert led.release(11, 100, starttime=1) == 0
    assert led.refcount(11) == 0
    assert led.state(11) == "reclaimable"


def test_release_is_idempotent_and_unknown_inode_is_quiet():
    led = ledger()
    led.track(11, 100, starttime=1)
    assert led.release(11, 100, starttime=1) == 0
    assert led.release(11, 100, starttime=1) == 0
    assert led.refcount(11) == 0
    assert led.release(99, 5) == 0
    assert led.state(99) == "unknown"


def test_audit_marks_missing_holders_stale():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.track(11, 101, starttime=2)
    out = led.audit([{"inode": 11, "pid": 100, "starttime": 1}])
    assert out["stale"] == [{"inode": 11, "pid": 101}]
    assert out["unmanaged"] == []
    assert led.refcount(11) == 1
    assert led.holders(11) == [holder(100, 1)]
    assert led.stats()["stale"] == 1


def test_audit_registers_unmanaged_holders_without_leak():
    led = ledger()
    led.track(11, 100, starttime=1)
    out = led.audit([
        {"inode": 11, "pid": 100, "starttime": 1},
        {"inode": 11, "pid": 102, "starttime": 3},
    ])
    assert out["unmanaged"] == [{"inode": 11, "pid": 102}]
    assert led.refcount(11) == 2
    assert holder(102, 3, "unmanaged") in led.holders(11)
    assert led.stats()["unmanaged"] == 1
    assert led.leaks() == []


def test_leak_requires_two_consecutive_audits():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.release(11, 100, starttime=1)
    assert led.state(11) == "reclaimable"
    led.audit([])
    assert led.leaks() == []
    led.audit([])
    assert led.leaks() == [11]


def test_bind_mount_holds_namespace_until_unmounted():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.release(11, 100, starttime=1)
    assert led.bind_mount(11, "/mnt/x") == 1
    assert led.state(11) == "mounted"
    assert led.unmount("/mnt/x") == 0
    assert led.state(11) == "reclaimable"


def test_reclaim_reports_inode_last_holder_and_time():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.release(11, 100, starttime=1)
    led.audit([])
    led.audit([])
    out = led.reclaim(now="2026-11-25T00:00:00Z")
    assert out == [{"inode": 11, "last_pid": 100,
                    "at": "2026-11-25T00:00:00Z"}]
    assert led.state(11) == "reclaimed"
    assert led.reclaim(now="2026-11-25T01:00:00Z") == []


def test_mounted_namespace_is_not_reclaimable():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.release(11, 100, starttime=1)
    led.bind_mount(11, "/mnt/y")
    led.audit([])
    led.audit([])
    assert led.leaks() == []
    assert led.reclaim(now="t") == []
    assert led.state(11) == "mounted"


def test_namespace_and_holder_caps_are_enforced():
    led = ledger(max_namespaces=1)
    led.track(11, 1, starttime=1)
    with pytest.raises(nrl.NsLedgerError) as excinfo:
        led.track(12, 2, starttime=1)
    assert excinfo.value.code == "NS_LIMIT"

    narrow = ledger(max_holders=1)
    narrow.track(11, 1, starttime=1)
    with pytest.raises(nrl.NsLedgerError) as excinfo:
        narrow.track(11, 2, starttime=2)
    assert excinfo.value.code == "HOLDER_LIMIT"


def test_multiple_paths_merge_into_one_namespace_record():
    led = ledger()
    led.track(11, 100, starttime=1, path="/proc/1/ns/net")
    led.track(11, 101, starttime=2, path="/proc/2/ns/net")
    assert led.paths(11) == ["/proc/1/ns/net", "/proc/2/ns/net"]
    assert len(led.holders(11)) == 2
    assert led.stats()["namespaces"] == 1


def test_snapshot_restore_keeps_holders_and_stale():
    led = ledger()
    led.track(11, 100, starttime=1)
    led.track(11, 101, starttime=2)
    led.audit([{"inode": 11, "pid": 100, "starttime": 1}])
    snap = led.snapshot()

    other = ledger()
    other.restore(snap)
    assert other.refcount(11) == 1
    assert other.holders(11) == [holder(100, 1)]
    assert other.stats()["holders"] == 1
    assert other.stats()["stale"] == 1
