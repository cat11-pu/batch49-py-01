import pytest

import cgroup_freeze_coordinator as cg


def coord(**kwargs):
    return cg.CgroupFreezeCoordinator(**kwargs)


def test_add_group_defaults_and_status():
    c = coord()
    view = c.add_group("/svc/a")
    assert view.path == "/svc/a"
    assert view.state == "thawed"
    assert view.frozen_ms == 0
    assert view.procs == 0
    assert view.quota_cpu == 100
    assert view.quota_mem == 1024
    c.set_processes("/svc/a", running=4)
    assert c.status("/svc/a").procs == 4
    assert c.usage("/svc/a") == {"used_ms": 0, "frozen_ms": 0}
    with pytest.raises(cg.FreezeError) as exc:
        c.status("/svc/missing")
    assert exc.value.code == "FREEZE_UNKNOWN_GROUP"


def test_freeze_snapshots_usage_and_state():
    c = coord()
    c.add_group("/a")
    c.sample_usage("/a", used_ms=100, now=10)
    view = c.freeze("/a", now=20)
    assert view.state == "frozen"
    assert c.status("/a").state == "frozen"
    assert c.usage("/a")["used_ms"] == 100
    assert c.journal()[-1] == {"path": "/a", "phase": "frozen", "ts": 20}
    assert c.stats()["freeze_events"] == 1


def test_freeze_twice_is_idempotent():
    c = coord()
    c.add_group("/a")
    c.freeze("/a", now=100)
    c.freeze("/a", now=400)
    c.thaw("/a", now=500)
    assert c.usage("/a")["frozen_ms"] == 400
    assert c.stats()["freeze_events"] == 1
    assert c.stats()["thaw_events"] == 1


def test_thaw_excludes_frozen_time_from_usage():
    c = coord()
    c.add_group("/a")
    c.sample_usage("/a", used_ms=100, now=50)
    c.freeze("/a", now=100)
    with pytest.raises(cg.FreezeError) as exc:
        c.sample_usage("/a", used_ms=120, now=200)
    assert exc.value.code == "FREEZE_SAMPLE_REJECTED"
    c.thaw("/a", now=600)
    c.sample_usage("/a", used_ms=160, now=700)
    assert c.usage("/a") == {"used_ms": 160, "frozen_ms": 500}


def test_freeze_refused_when_processes_exiting():
    c = coord()
    c.add_group("/a")
    c.set_processes("/a", running=2, exiting=1)
    with pytest.raises(cg.FreezeError) as exc:
        c.freeze("/a", now=10)
    assert exc.value.code == "FREEZE_PROCS_EXITING"
    assert exc.value.detail == "/a"
    assert c.status("/a").state == "thawed"
    assert c.stats()["freeze_events"] == 0


def test_zombies_only_warn_on_freeze():
    c = coord()
    c.add_group("/a")
    c.set_processes("/a", running=5, zombies=3)
    assert c.freeze("/a", now=10).state == "frozen"
    assert c.stats()["zombie_warnings"] == 3


def test_forking_retries_then_refuses():
    c = coord(retry_limit=2)
    c.add_group("/a")
    c.set_processes("/a", running=1, forking=1)
    with pytest.raises(cg.FreezeError) as exc:
        c.freeze("/a", now=10)
    assert exc.value.code == "FREEZE_FORK_RETRY_EXHAUSTED"
    assert c.stats()["fork_retries"] == 2
    assert c.status("/a").state == "thawed"


def test_thawing_state_blocks_freeze():
    c = coord()
    c.add_group("/a")
    c.freeze("/a", now=100)
    view = c.thaw("/a", now=500, finalize=False)
    assert view.state == "thawing"
    with pytest.raises(cg.FreezeError) as exc:
        c.freeze("/a", now=550)
    assert exc.value.code == "FREEZE_BUSY"
    final = c.thaw("/a", now=600, finalize=True)
    assert final.state == "thawed"
    assert c.usage("/a")["frozen_ms"] == 500
    assert c.stats()["thaw_events"] == 1


def test_resize_refused_while_frozen():
    c = coord()
    c.add_group("/a")
    c.freeze("/a", now=10)
    with pytest.raises(cg.FreezeError) as exc:
        c.resize("/a", quota_cpu=300, now=20)
    assert exc.value.code == "FREEZE_RESIZE_FORBIDDEN"
    assert c.status("/a").quota_cpu == 100
    c.thaw("/a", now=30)
    assert c.resize("/a", quota_cpu=300, quota_mem=4096, now=40).quota_cpu == 300


def test_batch_freeze_rolls_back_on_failure():
    c = coord()
    for path in ("/a", "/b", "/c"):
        c.add_group(path)
    c.freeze("/a", now=5)
    c.set_processes("/c", running=1, exiting=2)
    with pytest.raises(cg.FreezeError) as exc:
        c.batch_freeze(["/a", "/b", "/c"], now=20)
    assert exc.value.code == "FREEZE_BATCH_FAILED"
    assert exc.value.detail["failed"] == "/c"
    assert exc.value.detail["rolled_back"] == ["/b"]
    assert c.status("/a").state == "frozen"
    assert c.status("/b").state == "thawed"
    assert c.usage("/b")["frozen_ms"] == 0
    assert c.stats()["rollbacks"] == 1


def test_batch_freeze_success_keeps_order():
    c = coord()
    c.add_group("/a")
    c.add_group("/b")
    result = c.batch_freeze(["/b", "/a"], now=10)
    assert result == {"frozen": ["/b", "/a"], "rolled_back": []}
    assert c.status("/a").state == "frozen"
    assert c.status("/b").state == "frozen"
    assert c.stats()["freeze_events"] == 2


def test_batch_and_proc_caps():
    c = coord(max_batch=2, max_procs=16)
    c.add_group("/a")
    c.add_group("/b")
    c.add_group("/c")
    with pytest.raises(cg.FreezeError) as exc:
        c.batch_freeze(["/a", "/b", "/c"], now=10)
    assert exc.value.code == "FREEZE_BATCH_TOO_LARGE"
    assert c.status("/a").state == "thawed"
    with pytest.raises(cg.FreezeError) as exc2:
        c.add_group("/big", procs=17)
    assert exc2.value.code == "FREEZE_PROC_LIMIT_EXCEEDED"


def test_journal_intent_and_recover_convergence():
    c = coord()
    c.add_group("/a")
    c.add_group("/b")
    c.freeze("/a", now=10)
    c.freeze("/b", now=12)
    phases = [entry["phase"] for entry in c.journal() if entry["path"] == "/a"]
    assert phases == ["freezing", "frozen"]
    result = c.recover([
        {"path": "/a", "phase": "freezing", "ts": 30},
        {"path": "/b", "phase": "thawing", "ts": 31},
    ])
    assert result == {"converged": 2, "states": {"/a": "thawed", "/b": "thawed"}}
    assert c.status("/a").state == "thawed"
    assert c.status("/b").state == "thawed"
    assert c.stats()["recoveries"] == 2


def test_forget_removes_group():
    c = coord()
    c.add_group("/a")
    assert c.forget("/a") is True
    with pytest.raises(cg.FreezeError) as exc:
        c.status("/a")
    assert exc.value.code == "FREEZE_UNKNOWN_GROUP"
    assert c.forget("/a") is False


def test_batch_thaw_rolls_back_on_non_frozen():
    c = coord()
    c.add_group("/a")
    c.add_group("/b")
    c.freeze("/a", now=10)
    with pytest.raises(cg.FreezeError) as exc:
        c.batch_thaw(["/a", "/b"], now=20)
    assert exc.value.code == "FREEZE_BATCH_FAILED"
    assert exc.value.detail["failed"] == "/b"
    assert exc.value.detail["rolled_back"] == ["/a"]
    assert c.status("/a").state == "frozen"
    assert c.stats()["rollbacks"] == 1
    assert c.stats()["freeze_events"] == 1
    assert c.stats()["thaw_events"] == 0
