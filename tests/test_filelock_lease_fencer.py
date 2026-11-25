import os
import threading

import pytest

import filelock_lease_fencer as lf


def make(tmp_path, **kwargs):
    kwargs.setdefault("lease_seconds", 30)
    kwargs.setdefault("min_lease", 5)
    return lf.FileLockLeaseFencer(str(tmp_path / "writer.lock"), **kwargs)


def test_acquire_on_free_lock_grants_first_token(tmp_path):
    f = make(tmp_path)
    got = f.acquire("alpha", now=0)
    assert got["token"] == 1
    assert got["owner"] == "alpha"
    assert got["expires_at"] == 30
    assert got["reason"] == "acquired"
    assert f.lease()["owner"] == "alpha"


def test_acquire_while_held_reports_holder_and_expiry(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    with pytest.raises(lf.LeaseError) as err:
        f.acquire("beta", now=10)
    assert err.value.code == "held"
    assert err.value.detail["owner"] == "alpha"
    assert err.value.detail["expires_at"] == 30


def test_renew_by_non_holder_is_rejected(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    with pytest.raises(lf.LeaseError) as err:
        f.renew("beta", 1, now=5)
    assert err.value.code == "not_owner"


def test_renew_with_wrong_token_is_rejected(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    with pytest.raises(lf.LeaseError) as err:
        f.renew("alpha", 99, now=5)
    assert err.value.code == "bad_token"


def test_renew_extends_expiry_from_now(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    lease = f.renew("alpha", 1, now=10)
    assert lease["expires_at"] == 40
    assert f.remaining(now=10) == 30


def test_expired_lease_is_preempted_with_larger_token(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    got = f.acquire("beta", now=31)
    assert got["token"] == 2
    assert got["reason"] == "preempted"
    assert f.lease()["owner"] == "beta"


def test_guarded_write_appends_when_token_is_current(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    written = f.guarded_write("alpha", 1, "hello")
    assert written == 6
    with open(f.lock_path + ".data", "r", encoding="utf-8") as fh:
        assert fh.read() == "hello\n"


def test_release_clears_the_lease(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    f.release("alpha", 1)
    assert f.lease() is None
    with pytest.raises(lf.LeaseError) as err:
        f.renew("alpha", 1, now=5)
    assert err.value.code == "no_lease"


def test_token_stays_monotonic_after_release(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    f.release("alpha", 1)
    got = f.acquire("beta", now=5)
    assert got["token"] == 2


def test_release_by_non_holder_is_rejected(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    with pytest.raises(lf.LeaseError) as err:
        f.release("beta", 1)
    assert err.value.code == "not_owner"
    assert f.lease()["owner"] == "alpha"


def test_construction_rejects_lease_shorter_than_minimum(tmp_path):
    with pytest.raises(lf.LeaseError) as err:
        lf.FileLockLeaseFencer(str(tmp_path / "x.lock"), lease_seconds=1, min_lease=5)
    assert err.value.code == "lease_too_short"


def test_snapshot_restore_does_not_regress_token(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    snap = f.snapshot()
    g = make(tmp_path)
    g.restore(snap)
    assert g.token() == 1
    got = g.acquire("beta", now=31)
    assert got["token"] == 2


def test_concurrent_acquire_has_single_winner(tmp_path):
    f = make(tmp_path)
    barrier = threading.Barrier(20)
    outcomes = []
    lock = threading.Lock()

    def worker(i):
        barrier.wait()
        try:
            f.acquire(f"owner-{i}", now=0)
            result = "won"
        except lf.LeaseError as exc:
            result = exc.code
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("won") == 1
    assert outcomes.count("held") == 19


def test_renew_survives_external_lock_file_removal(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    os.remove(f.lock_path)
    lease = f.renew("alpha", 1, now=10)
    assert lease["expires_at"] == 40


def test_history_records_each_grant_with_reason(tmp_path):
    f = make(tmp_path)
    f.acquire("alpha", now=0)
    f.acquire("beta", now=31)
    events = f.history()
    assert len(events) == 2
    assert [e["token"] for e in events] == [1, 2]
    assert [e["owner"] for e in events] == ["alpha", "beta"]
    assert [e["reason"] for e in events] == ["acquired", "preempted"]
    assert events[0]["time"] == 0
