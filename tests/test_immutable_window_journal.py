import hashlib
import threading

import pytest

import immutable_window_journal as iwj

ROOT = "/data"


def journal(**kwargs):
    kwargs.setdefault("protected_roots", (ROOT,))
    return iwj.ImmutableWindowJournal(**kwargs)


def test_add_file_registers_content_and_digest_helper():
    obj = journal()
    record = obj.add_file("/data/app.conf", content="v0")
    assert record.path == "/data/app.conf"
    assert record.inode == 1
    assert record.content == "v0"
    assert record.immutable is True
    assert record.backup_id is None
    assert obj.file_info("/data/app.conf") == {
        "path": "/data/app.conf",
        "inode": 1,
        "content": "v0",
        "immutable": True,
        "backup_id": None,
    }
    assert obj.windows() == []
    assert iwj.content_digest("v0") == hashlib.sha256(b"v0").hexdigest()


def test_open_window_clears_immutable_and_journals_first():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    win = obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    assert win.window_id == "w-001"
    assert win.path == "/data/app.conf"
    assert win.owner == "deploy"
    assert win.ttl == 60
    assert win.opened_at == 100
    assert win.deadline == 160
    assert win.state == "open"
    assert win.backup_id == "bak-w-001"
    assert obj.file_info("/data/app.conf")["immutable"] is False
    entries = obj.journal_entries()
    assert [(entry["op"], entry["state"], entry["window"]) for entry in entries] == [
        ("open", "done", "w-001")]


def test_open_window_requires_immutable_and_exclusive_path():
    obj = journal()
    obj.add_file("/data/app.conf")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    with pytest.raises(iwj.WindowError) as exc:
        obj.open_window("/data/app.conf", owner="audit", ttl=30, now=101)
    assert exc.value.code == "WINDOW_EXISTS"
    assert len(obj.windows()) == 1

    obj.add_file("/data/second.conf", immutable=False)
    with pytest.raises(iwj.WindowError) as exc:
        obj.open_window("/data/second.conf", owner="deploy", ttl=30, now=101)
    assert exc.value.code == "NOT_IMMUTABLE"
    assert obj.file_info("/data/second.conf")["immutable"] is False


def test_window_request_validation():
    obj = journal(max_duration=600)
    obj.add_file("/data/app.conf")
    bad_calls = [
        (dict(owner="", ttl=60), "INVALID_REQUEST"),
        (dict(owner="deploy", ttl=0), "INVALID_REQUEST"),
        (dict(owner="deploy", ttl=-5), "INVALID_REQUEST"),
        (dict(owner="deploy", ttl=601), "DURATION_LIMIT"),
    ]
    for kwargs, expected in bad_calls:
        with pytest.raises(iwj.WindowError) as exc:
            obj.open_window("/data/app.conf", now=100, **kwargs)
        assert exc.value.code == expected
    assert obj.windows() == []
    assert obj.file_info("/data/app.conf")["immutable"] is True


def test_window_count_cap():
    obj = journal(max_windows=2)
    for index in range(3):
        obj.add_file(f"/data/f{index}.conf")
    obj.open_window("/data/f0.conf", owner="ops", ttl=60, now=100)
    obj.open_window("/data/f1.conf", owner="ops", ttl=60, now=100)
    with pytest.raises(iwj.WindowError) as exc:
        obj.open_window("/data/f2.conf", owner="ops", ttl=60, now=100)
    assert exc.value.code == "WINDOW_LIMIT"
    assert obj.file_info("/data/f2.conf")["immutable"] is True


def test_owner_write_and_intruder_write_are_recorded():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    mine = obj.write("/data/app.conf", window_id="w-001", writer="deploy",
                     data="v1", now=110)
    assert mine == {"path": "/data/app.conf", "window": "w-001",
                    "violation": False, "digest": iwj.content_digest("v1")}
    theirs = obj.write("/data/app.conf", window_id="w-001", writer="cron",
                       data="v2", now=120)
    assert theirs["violation"] is True
    assert obj.file_info("/data/app.conf")["content"] == "v2"
    assert obj.stats()["violations"] == 1


def test_write_after_window_closed_is_refused():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.commit("w-001", digest=iwj.content_digest("v0"), now=120)
    with pytest.raises(iwj.WindowError) as exc:
        obj.write("/data/app.conf", window_id="w-001", writer="deploy",
                  data="late", now=130)
    assert exc.value.code == "WINDOW_CLOSED"
    assert obj.file_info("/data/app.conf")["content"] == "v0"


def test_commit_digest_mismatch_keeps_window_writable():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.write("/data/app.conf", window_id="w-001", writer="deploy",
              data="v1", now=110)
    with pytest.raises(iwj.WindowError) as exc:
        obj.commit("w-001", digest=iwj.content_digest("v0"), now=120)
    assert exc.value.code == "DIGEST_MISMATCH"
    assert obj.windows()[0].state == "open"
    assert obj.file_info("/data/app.conf")["immutable"] is False
    obj.write("/data/app.conf", window_id="w-001", writer="deploy",
              data="v2", now=121)
    closed = obj.commit("w-001", digest=iwj.content_digest("v2"), now=122)
    assert closed.state == "committed"
    assert obj.file_info("/data/app.conf")["content"] == "v2"


def test_commit_locks_file_and_reports_stats():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.write("/data/app.conf", window_id="w-001", writer="deploy",
              data="v1", now=110)
    out = obj.commit("w-001", digest=iwj.content_digest("v1"), now=120)
    assert out.state == "committed"
    assert out.deadline == 160
    assert obj.file_info("/data/app.conf")["immutable"] is True
    assert obj.windows() == [out]
    assert obj.windows(state="open") == []
    assert obj.stats() == {"opened": 1, "committed": 1, "aborted": 0,
                           "expired": 0, "violations": 0, "failed": 0,
                           "recovered": 0}


def test_abort_restores_backup_content():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.write("/data/app.conf", window_id="w-001", writer="deploy",
              data="broken", now=110)
    out = obj.abort("w-001", reason="manual", now=120)
    assert out.state == "aborted"
    assert obj.file_info("/data/app.conf")["content"] == "v0"
    assert obj.file_info("/data/app.conf")["immutable"] is True
    assert obj.stats()["aborted"] == 1
    assert obj.stats()["expired"] == 0
    assert [entry["op"] for entry in obj.journal_entries()] == ["open", "abort"]


def test_abort_without_backup_is_refused():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.drop_backup("w-001")
    with pytest.raises(iwj.WindowError) as exc:
        obj.abort("w-001", now=110)
    assert exc.value.code == "BACKUP_MISSING"
    assert obj.windows()[0].state == "open"
    assert obj.file_info("/data/app.conf")["immutable"] is False


def test_sweep_aborts_only_overdue_windows():
    obj = journal()
    obj.add_file("/data/fast.conf", content="a")
    obj.add_file("/data/slow.conf", content="b")
    obj.open_window("/data/fast.conf", owner="deploy", ttl=10, now=100)
    obj.open_window("/data/slow.conf", owner="deploy", ttl=100, now=100)
    report = obj.sweep(now=110)
    assert report == {"expired": ("w-001",), "aborted": 1}
    assert obj.windows()[0].state == "aborted"
    assert obj.windows()[1].state == "open"
    assert obj.file_info("/data/fast.conf")["immutable"] is True
    assert obj.file_info("/data/fast.conf")["content"] == "a"
    assert obj.file_info("/data/slow.conf")["immutable"] is False
    assert obj.stats()["expired"] == 1
    assert obj.stats()["aborted"] == 1
    assert obj.sweep(now=111) == {"expired": (), "aborted": 0}


def test_sweep_races_commit_with_single_winner():
    obj = journal()
    obj.add_file("/data/app.conf", content="v0")
    obj.open_window("/data/app.conf", owner="deploy", ttl=60, now=100)
    obj.write("/data/app.conf", window_id="w-001", writer="deploy",
              data="v1", now=150)
    digest = iwj.content_digest("v1")
    outcomes = {}
    barrier = threading.Barrier(2)

    def do_commit():
        barrier.wait(timeout=5)
        try:
            obj.commit("w-001", digest=digest, now=160)
            outcomes["commit"] = "committed"
        except iwj.WindowError as exc:
            outcomes["commit"] = exc.code

    def do_sweep():
        barrier.wait(timeout=5)
        outcomes["sweep"] = obj.sweep(now=160)

    threads = [threading.Thread(target=do_commit),
               threading.Thread(target=do_sweep)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = int(outcomes["commit"] == "committed")
    winners += int(outcomes["sweep"]["aborted"] == 1)
    assert winners == 1
    window = obj.windows()[0]
    assert window.state in ("committed", "aborted")
    if window.state == "aborted":
        assert outcomes["commit"] == "WINDOW_CLOSED"
        assert obj.file_info("/data/app.conf")["content"] == "v0"
    else:
        assert outcomes["sweep"] == {"expired": (), "aborted": 0}
        assert obj.file_info("/data/app.conf")["content"] == "v1"
    assert obj.file_info("/data/app.conf")["immutable"] is True
    assert obj.stats()["committed"] + obj.stats()["expired"] == 1


def test_recover_aborts_unfinished_windows_after_restart():
    obj = journal()
    obj.add_file("/data/a.conf", content="a0")
    obj.add_file("/data/b.conf", content="b0")
    obj.open_window("/data/a.conf", owner="deploy", ttl=60, now=100)
    obj.open_window("/data/b.conf", owner="deploy", ttl=60, now=100)
    obj.write("/data/b.conf", window_id="w-002", writer="deploy",
              data="b1", now=110)
    obj.commit("w-002", digest=iwj.content_digest("b1"), now=120)
    state = obj.snapshot()

    clone = journal()
    clone.restore(state)
    report = clone.recover(now=130)
    assert report == {"scanned": 2, "aborted": 1}
    assert clone.windows()[0].state == "aborted"
    assert clone.windows()[1].state == "committed"
    assert clone.file_info("/data/a.conf")["immutable"] is True
    assert clone.file_info("/data/a.conf")["content"] == "a0"
    assert clone.file_info("/data/b.conf")["content"] == "b1"
    assert clone.stats()["recovered"] == 1


def test_restore_immutable_retries_then_marks_failed():
    obj = journal(max_retries=3)
    obj.add_file("/data/app.conf", content="v0")
    obj.inject_fault("/data/app.conf", code="READBACK_MISMATCH", times=3)
    out = obj.restore_immutable("/data/app.conf", now=100)
    assert out == {"ok": False, "attempts": 3, "alerts": 1}
    assert obj.stats()["failed"] == 1
    assert obj.file_info("/data/app.conf")["immutable"] is False

    obj.inject_fault("/data/app.conf", code="READBACK_MISMATCH", times=1)
    ok = obj.restore_immutable("/data/app.conf", now=101)
    assert ok == {"ok": True, "attempts": 2, "alerts": 0}
    assert obj.file_info("/data/app.conf")["immutable"] is True
