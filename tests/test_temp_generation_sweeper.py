import threading

import pytest

import temp_generation_sweeper as tgs

ROOT = "/tmp/work"


def sweeper(**kwargs):
    kwargs.setdefault("root", ROOT)
    return tgs.TempGenerationSweeper(**kwargs)


def test_begin_generation_freezes_previous():
    obj = sweeper()
    assert obj.current_generation() == 0
    first = obj.begin_generation(label="build-1", now=0)
    assert first.gen == 1
    assert first.label == "build-1"
    assert first.state == "active"
    assert first.created_at == 0
    assert obj.current_generation() == 1

    second = obj.begin_generation(label=None, now=5)
    assert second.gen == 2
    assert second.state == "active"
    assert second.created_at == 5
    assert obj.current_generation() == 2
    assert obj.freeze(1) == tgs.Generation(gen=1, label="build-1",
                                           state="frozen", created_at=0)
    assert obj.dirs() == []


def test_create_assigns_active_generation_and_validates():
    obj = sweeper()
    obj.begin_generation(now=0)
    entry = obj.create("/tmp/work/g1/job/a.tmp", bytes=10)
    assert entry.path == "/tmp/work/g1/job/a.tmp"
    assert entry.gen == 1
    assert entry.inode == 1
    assert entry.bytes == 10
    assert [row.path for row in obj.dirs()] == ["/tmp/work/g1/job/a.tmp"]

    with pytest.raises(tgs.SweepError) as exc:
        obj.create("/tmp/work/g1/negative", bytes=-1)
    assert exc.value.code == "INVALID_REQUEST"
    with pytest.raises(tgs.SweepError) as exc:
        obj.create("/tmp/work/g1/job/a.tmp")
    assert exc.value.code == "DIR_EXISTS"
    with pytest.raises(tgs.SweepError) as exc:
        obj.create("/tmp/other/g1/job")
    assert exc.value.code == "PATH_OUTSIDE"
    assert len(obj.dirs()) == 1


def test_create_after_freeze_lands_in_new_generation():
    obj = sweeper()
    obj.begin_generation(label="old", now=0)
    old = obj.create("/tmp/work/g1/job", bytes=3)
    obj.begin_generation(label="new", now=1)
    new = obj.create("/tmp/work/g2/job", bytes=4)
    assert new.gen == 2
    assert new.inode == 2
    assert obj.dirs(gen=1) == [old]
    assert obj.dirs(gen=2) == [new]
    assert obj.freeze(1).state == "frozen"


def test_directory_and_byte_caps_per_generation():
    obj = sweeper(max_dirs_per_gen=2, max_bytes=1000)
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job0", bytes=1)
    obj.create("/tmp/work/g1/job1", bytes=1)
    with pytest.raises(tgs.SweepError) as exc:
        obj.create("/tmp/work/g1/job2", bytes=1)
    assert exc.value.code == "DIR_LIMIT"
    assert len(obj.dirs()) == 2

    tight = sweeper(max_bytes=15)
    tight.begin_generation(now=0)
    tight.create("/tmp/work/g1/job0", bytes=10)
    with pytest.raises(tgs.SweepError) as exc:
        tight.create("/tmp/work/g1/job1", bytes=6)
    assert exc.value.code == "BYTE_LIMIT"
    assert [row.path for row in tight.dirs()] == ["/tmp/work/g1/job0"]


def test_lease_registration_and_release():
    obj = sweeper()
    obj.begin_generation(now=0)
    path = "/tmp/work/g1/job"
    obj.create(path)
    with pytest.raises(tgs.SweepError) as exc:
        obj.acquire("/tmp/work/g1/missing", lease="l1")
    assert exc.value.code == "PATH_UNKNOWN"

    obj.acquire(path, lease="l1")
    with pytest.raises(tgs.SweepError) as exc:
        obj.acquire(path, lease="l1")
    assert exc.value.code == "LEASE_EXISTS"
    obj.acquire(path, lease="l2")
    obj.release(path, lease="l1")
    obj.release(path, lease="l2")
    with pytest.raises(tgs.SweepError) as exc:
        obj.release(path, lease="l1")
    assert exc.value.code == "LEASE_UNKNOWN"
    obj.acquire(path, lease="l1")
    obj.release(path, lease="l1")


def test_sweep_only_touches_frozen_generations():
    obj = sweeper()
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job", bytes=1)
    obj.begin_generation(now=1)
    obj.create("/tmp/work/g2/job", bytes=1)
    report = obj.sweep()
    assert report["deleted"] == ("/tmp/work/g1/job",)
    assert [row.path for row in obj.dirs()] == ["/tmp/work/g2/job"]
    assert obj.stats()["deleted"] == 1

    with pytest.raises(tgs.SweepError) as exc:
        obj.sweep(gen=2)
    assert exc.value.code == "GEN_ACTIVE"
    with pytest.raises(tgs.SweepError) as exc:
        obj.sweep(gen=9)
    assert exc.value.code == "GEN_UNKNOWN"


def test_sweep_deletes_children_before_parents():
    obj = sweeper()
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job", bytes=1)
    obj.create("/tmp/work/g1/job/tmp", bytes=1)
    obj.create("/tmp/work/g1/job/tmp/a.bin", bytes=1)
    obj.create("/tmp/work/g1/job/tmp/b.bin", bytes=1)
    obj.begin_generation(now=1)
    report = obj.sweep(gen=1)
    assert report["deleted"] == (
        "/tmp/work/g1/job/tmp/a.bin",
        "/tmp/work/g1/job/tmp/b.bin",
        "/tmp/work/g1/job/tmp",
        "/tmp/work/g1/job",
    )
    assert obj.dirs() == []
    assert obj.stats()["deleted"] == 4


def test_active_lease_blocks_deletion_until_released():
    obj = sweeper()
    obj.begin_generation(now=0)
    path = "/tmp/work/g1/job"
    obj.create(path, bytes=1)
    obj.begin_generation(now=1)
    obj.acquire(path, lease="l1")
    report = obj.sweep(gen=1)
    assert report["deleted"] == ()
    assert report["skipped"] == (path,)
    assert [row.path for row in obj.dirs()] == [path]

    obj.release(path, lease="l1")
    report = obj.sweep(gen=1)
    assert report["deleted"] == (path,)
    assert report["skipped"] == ()
    assert obj.dirs() == []


def test_repeated_sweep_counts_previous_deletions_as_skipped():
    obj = sweeper()
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job0", bytes=1)
    obj.create("/tmp/work/g1/job1", bytes=1)
    obj.begin_generation(now=1)
    first = obj.sweep(gen=1)
    assert first["deleted"] == ("/tmp/work/g1/job0", "/tmp/work/g1/job1")
    second = obj.sweep(gen=1)
    assert second["deleted"] == ()
    assert second["skipped"] == ("/tmp/work/g1/job0", "/tmp/work/g1/job1")
    assert obj.stats() == {"deleted": 2, "skipped": 2, "quarantined": 0,
                           "failed": 0, "delayed": 0}


def test_failed_directory_moves_to_quarantine():
    obj = sweeper()
    obj.begin_generation(now=0)
    path = "/tmp/work/g1/job"
    obj.create(path, bytes=1)
    obj.begin_generation(now=1)
    obj.inject_fault(path, code="EACCES", times=1)
    report = obj.sweep(gen=1)
    assert report["deleted"] == ()
    assert report["quarantined"] == (path,)
    assert obj.dirs() == []
    assert obj.quarantine_report() == [
        {"path": path, "gen": 1, "inode": 1, "code": "EACCES",
         "attempts": 1}
    ]
    assert obj.stats()["quarantined"] == 1


def test_retry_quarantine_keeps_then_deletes():
    obj = sweeper()
    obj.begin_generation(now=0)
    path = "/tmp/work/g1/job"
    obj.create(path, bytes=1)
    obj.begin_generation(now=1)
    obj.inject_fault(path, code="EACCES", times=2)
    obj.sweep(gen=1)
    assert obj.quarantine_report()[0]["attempts"] == 1

    assert obj.retry_quarantine() == {"deleted": 0, "retained": 1}
    assert obj.quarantine_report()[0]["attempts"] == 2
    assert obj.retry_quarantine() == {"deleted": 1, "retained": 0}
    assert obj.quarantine_report() == []
    assert obj.dirs() == []
    assert obj.stats()["deleted"] == 1


def test_quarantine_limit_is_enforced():
    obj = sweeper(quarantine_limit=1)
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job0", bytes=1)
    obj.create("/tmp/work/g1/job1", bytes=1)
    obj.begin_generation(now=1)
    obj.inject_fault("/tmp/work/g1/job0", code="EACCES", times=1)
    obj.inject_fault("/tmp/work/g1/job1", code="EACCES", times=1)
    with pytest.raises(tgs.SweepError) as exc:
        obj.sweep(gen=1)
    assert exc.value.code == "QUARANTINE_LIMIT"
    assert [row["path"] for row in obj.quarantine_report()] == [
        "/tmp/work/g1/job0"]
    assert [row.path for row in obj.dirs()] == ["/tmp/work/g1/job1"]
    assert obj.stats()["quarantined"] == 1
    assert obj.stats()["failed"] == 1


def test_stats_reconcile_all_categories():
    obj = sweeper()
    obj.begin_generation(now=0)
    obj.create("/tmp/work/g1/job0", bytes=1)
    obj.create("/tmp/work/g1/job1", bytes=1)
    obj.create("/tmp/work/g1/job2", bytes=1)
    obj.begin_generation(now=1)
    obj.acquire("/tmp/work/g1/job1", lease="l1")
    obj.inject_fault("/tmp/work/g1/job2", code="EACCES", times=1)
    report = obj.sweep(gen=1)
    assert report["deleted"] == ("/tmp/work/g1/job0",)
    assert report["skipped"] == ("/tmp/work/g1/job1",)
    assert report["quarantined"] == ("/tmp/work/g1/job2",)
    assert report["failed"] == ()
    assert obj.stats() == {"deleted": 1, "skipped": 1, "quarantined": 1,
                           "failed": 0, "delayed": 0}
    assert [row.path for row in obj.dirs()] == ["/tmp/work/g1/job1"]


def test_snapshot_restore_keeps_frozen_generation_cleanable():
    obj = sweeper()
    obj.begin_generation(label="one", now=0)
    obj.create("/tmp/work/g1/job0", bytes=2)
    obj.create("/tmp/work/g1/job1", bytes=3)
    obj.begin_generation(label="two", now=1)
    obj.create("/tmp/work/g2/job", bytes=4)
    state = obj.snapshot()
    assert isinstance(state, dict)

    clone = sweeper()
    clone.restore(state)
    assert clone.current_generation() == 2
    assert [row.path for row in clone.dirs(gen=1)] == [
        "/tmp/work/g1/job0", "/tmp/work/g1/job1"]
    report = clone.sweep()
    assert report["deleted"] == ("/tmp/work/g1/job0", "/tmp/work/g1/job1")
    assert [row.path for row in clone.dirs()] == ["/tmp/work/g2/job"]
    assert clone.stats()["deleted"] == 2


def test_concurrent_lease_release_and_sweep():
    obj = sweeper()
    obj.begin_generation(label="build", now=0)
    paths = [f"/tmp/work/g1/job{index}" for index in range(4)]
    for path in paths:
        obj.create(path, bytes=1)
    obj.begin_generation(label="next", now=1)
    errors = []
    seen = {}
    barrier = threading.Barrier(3)

    def lease_worker(lease):
        try:
            barrier.wait(timeout=5)
            for path in paths:
                obj.acquire(path, lease=lease)
                obj.release(path, lease=lease)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    def sweep_worker():
        try:
            barrier.wait(timeout=5)
            seen["report"] = obj.sweep(gen=1)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=lease_worker, args=(f"l{index}",))
               for index in range(2)]
    threads.append(threading.Thread(target=sweep_worker))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    final = obj.sweep(gen=1)
    deleted = tuple(seen["report"]["deleted"]) + tuple(final["deleted"])
    assert sorted(deleted) == sorted(paths)
    assert len(deleted) == len(set(deleted))
    assert obj.stats()["deleted"] == 4
    assert obj.stats()["skipped"] == 4
    assert obj.dirs() == []
