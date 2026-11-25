import copy
import threading

import pytest

import totp_step_ledger as tl

ZERO_STATS = {
    "accounts": 0,
    "consumed": 0,
    "replays": 0,
    "rollbacks": 0,
    "epoch_rejects": 0,
    "lease_misses": 0,
    "lease_issues": 0,
    "lease_releases": 0,
    "lease_expiries": 0,
    "rebinds": 0,
}


def make_ledger(**kwargs):
    return tl.TotpStepLedger(**kwargs)


def make_account(ledger, name="alice", **overrides):
    config = dict(epoch=1, drift_steps=1, step_ms=10, retention_steps=8)
    config.update(overrides)
    return ledger.register_account(name, **config)


def consume(ledger, *, now, epoch=1, lease_id=None, name="alice"):
    return ledger.consume(name, epoch=epoch, now=now, lease_id=lease_id)


def test_construction_and_registration_validation():
    with pytest.raises(tl.TotpError) as exc:
        make_ledger(shard_capacity=0)
    assert exc.value.code == "TOTP_INVALID_REQUEST"
    with pytest.raises(tl.TotpError) as exc:
        make_ledger(lease_ttl_ms=0)
    assert exc.value.code == "TOTP_INVALID_REQUEST"

    ledger = make_ledger()
    assert ledger.stats() == ZERO_STATS
    for override in (dict(epoch=0), dict(drift_steps=-1), dict(step_ms=0),
                     dict(retention_steps=0)):
        with pytest.raises(tl.TotpError) as exc:
            make_account(ledger, "bad", **override)
        assert exc.value.code == "TOTP_INVALID_REQUEST"
    with pytest.raises(tl.TotpError) as exc:
        make_account(ledger, "")
    assert exc.value.code == "TOTP_INVALID_REQUEST"
    assert ledger.stats()["accounts"] == 0


def test_consume_for_unknown_account_is_refused():
    ledger = make_ledger()
    result = ledger.consume("ghost", epoch=1, now=0)
    assert (result.status, result.step, result.epoch) == ("unknown_account", None, None)
    assert ledger.stats()["consumed"] == 0


def test_single_step_replay_is_rejected():
    ledger = make_ledger()
    make_account(ledger, drift_steps=0)
    first = consume(ledger, now=0)
    assert (first.status, first.step, first.epoch) == ("ok", 0, 1)
    again = consume(ledger, now=0)
    assert (again.status, again.step, again.epoch) == ("replay", None, None)
    assert ledger.stats()["consumed"] == 1
    assert ledger.stats()["replays"] == 1
    assert ledger.consumed_steps("alice") == (0,)
    assert consume(ledger, now=10).step == 1


def test_window_consumes_lowest_unused_step_first():
    ledger = make_ledger()
    make_account(ledger, drift_steps=2)
    steps = [consume(ledger, now=20).step for _ in range(5)]
    assert steps == [0, 1, 2, 3, 4]
    exhausted = consume(ledger, now=20)
    assert (exhausted.status, exhausted.step) == ("replay", None)
    assert ledger.consumed_steps("alice") == (0, 1, 2, 3, 4)


def test_lease_reserves_steps_and_consume_honours_it():
    ledger = make_ledger()
    make_account(ledger, drift_steps=0)
    lease = ledger.lease("alice", now=0, count=3)
    assert (lease.account, lease.epoch) == ("alice", 1)
    assert lease.steps == (0, 1, 2)
    assert lease.expires_at == 30000
    assert ledger.stats()["lease_issues"] == 1

    result = consume(ledger, now=0, lease_id=lease.lease_id)
    assert (result.status, result.step) == ("ok", 0)
    assert ledger.consumed_steps("alice") == (0,)
    assert ledger.lease_of(lease.lease_id).steps == (1, 2)


def test_unknown_lease_id_cannot_consume():
    ledger = make_ledger()
    make_account(ledger, drift_steps=1)
    result = consume(ledger, now=0, lease_id="ls-made-up")
    assert (result.status, result.step, result.epoch) == ("lease_unusable", None, None)
    assert ledger.consumed_steps("alice") == ()
    assert ledger.stats()["lease_misses"] == 1


def test_release_returns_unused_steps_to_the_pool():
    ledger = make_ledger()
    make_account(ledger, drift_steps=0)
    lease = ledger.lease("alice", now=0, count=2)
    assert consume(ledger, now=0, lease_id=lease.lease_id).step == 0
    assert ledger.release(lease.lease_id, now=5) == 1
    assert ledger.lease_of(lease.lease_id) is None
    assert ledger.stats()["lease_releases"] == 1
    assert consume(ledger, now=10).step == 1


def test_release_unknown_lease_raises():
    ledger = make_ledger()
    make_account(ledger)
    with pytest.raises(tl.TotpError) as exc:
        ledger.release("nope", now=0)
    assert exc.value.code == "TOTP_LEASE_UNKNOWN"


def test_rebind_bumps_epoch_and_clears_consumed_steps():
    ledger = make_ledger()
    make_account(ledger, drift_steps=0)
    assert consume(ledger, now=0).status == "ok"
    assert ledger.rebind("alice", now=10) == 2
    assert ledger.consumed_steps("alice") == ()
    result = consume(ledger, now=10, epoch=2)
    assert (result.status, result.step, result.epoch) == ("ok", 1, 2)
    assert ledger.stats()["rebinds"] == 1
    with pytest.raises(tl.TotpError) as exc:
        ledger.rebind("ghost", now=10)
    assert exc.value.code == "TOTP_UNKNOWN_ACCOUNT"


def test_shard_capacity_blocks_new_consumption():
    ledger = make_ledger(shard_capacity=2)
    make_account(ledger, drift_steps=0)
    assert consume(ledger, now=0).status == "ok"
    assert consume(ledger, now=10).status == "ok"
    blocked = consume(ledger, now=20)
    assert (blocked.status, blocked.step) == ("capacity", None)
    assert ledger.consumed_steps("alice") == (0, 1)
    assert ledger.stats()["consumed"] == 2

    assert ledger.purge_expired(1000) == (2, 0)
    assert ledger.consumed_steps("alice") == ()
    assert consume(ledger, now=1000).status == "ok"


def test_lease_capacity_is_enforced():
    ledger = make_ledger(shard_capacity=2)
    make_account(ledger, drift_steps=0)
    with pytest.raises(tl.TotpError) as exc:
        ledger.lease("alice", now=0, count=3)
    assert exc.value.code == "TOTP_CAPACITY_EXCEEDED"
    lease = ledger.lease("alice", now=0, count=2)
    assert lease.steps == (0, 1)
    with pytest.raises(tl.TotpError) as exc:
        ledger.lease("alice", now=0, count=1)
    assert exc.value.code == "TOTP_CAPACITY_EXCEEDED"
    with pytest.raises(tl.TotpError) as exc:
        ledger.lease("ghost", now=0, count=1)
    assert exc.value.code == "TOTP_UNKNOWN_ACCOUNT"


def test_purge_expired_trims_old_steps_and_expired_leases():
    ledger = make_ledger(lease_ttl_ms=100)
    make_account(ledger, drift_steps=0, retention_steps=1)
    for now in (0, 10, 20):
        assert consume(ledger, now=now).status == "ok"
    lease = ledger.lease("alice", now=20, count=1)
    assert lease.steps == (3,)

    assert ledger.purge_expired(30) == (2, 0)
    assert ledger.consumed_steps("alice") == (2,)
    assert ledger.lease_of(lease.lease_id) is not None

    assert ledger.purge_expired(120) == (1, 1)
    assert ledger.consumed_steps("alice") == ()
    assert ledger.lease_of(lease.lease_id) is None
    assert ledger.stats()["lease_expiries"] == 1


def test_concurrent_consumes_take_distinct_steps():
    ledger = make_ledger()
    make_account(ledger, drift_steps=8)
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(consume(ledger, now=0))
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [row.status for row in results] == ["ok"] * 8
    assert sorted(row.step for row in results) == list(range(8))
    assert ledger.stats()["consumed"] == 8
    assert ledger.consumed_steps("alice") == tuple(range(8))


def test_snapshot_is_detached_and_restore_roundtrips():
    ledger = make_ledger(shard_capacity=8)
    make_account(ledger, drift_steps=0, retention_steps=4)
    assert consume(ledger, now=0).status == "ok"
    lease = ledger.lease("alice", now=10, count=2)
    assert lease.steps == (1, 2)

    state = ledger.snapshot()
    assert set(state) == {"version", "accounts", "consumed", "leases",
                          "high_water", "last_now", "stats"}
    pristine = copy.deepcopy(state)
    state["consumed"].clear()
    state["stats"]["consumed"] = 99
    assert ledger.stats()["consumed"] == 1
    assert ledger.consumed_steps("alice") == (0,)

    clone = make_ledger(shard_capacity=8)
    clone.restore(pristine)
    assert clone.stats()["consumed"] == 1
    assert clone.consumed_steps("alice") == (0,)
    assert clone.lease_of(lease.lease_id).steps == (1, 2)
    assert clone.consume("alice", epoch=1, now=10, lease_id=lease.lease_id).step == 1


def test_restore_rejects_malformed_state_and_keeps_live_data():
    ledger = make_ledger()
    make_account(ledger, drift_steps=0)
    assert consume(ledger, now=0).status == "ok"
    good = ledger.snapshot()
    without_version = {key: value for key, value in good.items() if key != "version"}

    bad_states = [
        None,
        {},
        {"version": 2, **without_version},
        {**good, "accounts": []},
        {**good, "consumed": {}},
        {**good, "stats": {"consumed": 1}},
        {**good, "leases": [{"lease_id": "x"}]},
        {**good, "last_now": "soon"},
    ]
    for bad in bad_states:
        with pytest.raises(tl.TotpError) as exc:
            ledger.restore(bad)
        assert exc.value.code == "TOTP_SNAPSHOT_INVALID"

    assert ledger.stats()["consumed"] == 1
    assert ledger.consumed_steps("alice") == (0,)
    assert consume(ledger, now=10).status == "ok"
