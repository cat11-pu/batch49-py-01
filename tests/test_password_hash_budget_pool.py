import copy
import threading

import pytest

import password_hash_budget_pool as ph

ZERO_STATS = {
    "verifies": 0,
    "ok": 0,
    "denied": 0,
    "dummy": 0,
    "queue_retries": 0,
    "budget_retries": 0,
    "locked": 0,
    "deferred": 0,
    "upgrades": 0,
    "upgrades_failed": 0,
}


def make_pool(**kwargs):
    return ph.PasswordHashBudgetPool(**kwargs)


def make_policy(pool, tenant="t1", **overrides):
    config = dict(per_tenant_concurrency=4, queue_deadline_ms=100, cost_ms=10,
                  window_ms=1000, budget_units=100, verify_units=1,
                  upgrade_units=4, lockout_threshold=3, lock_ms=60000)
    config.update(overrides)
    return pool.register_policy(tenant, **config)


def test_construction_and_policy_validation_reject_bad_numbers():
    with pytest.raises(ph.PasswordHashError) as exc:
        make_pool(pool_capacity=0)
    assert exc.value.code == "HASH_POLICY_INVALID"
    with pytest.raises(ph.PasswordHashError) as exc:
        make_pool(queue_limit=-1)
    assert exc.value.code == "HASH_POLICY_INVALID"

    pool = make_pool()
    assert pool.stats() == ZERO_STATS
    bad = [
        dict(per_tenant_concurrency=0),
        dict(queue_deadline_ms=-1),
        dict(cost_ms=0),
        dict(window_ms=0),
        dict(budget_units=0),
        dict(verify_units=0),
        dict(upgrade_units=0),
        dict(lockout_threshold=0),
        dict(lock_ms=0),
        dict(verify_units=500, budget_units=100),
    ]
    for override in bad:
        with pytest.raises(ph.PasswordHashError) as exc:
            make_policy(pool, tenant="bad", **override)
        assert exc.value.code == "HASH_POLICY_INVALID"
    with pytest.raises(ph.PasswordHashError) as exc:
        make_policy(pool, tenant="")
    assert exc.value.code == "HASH_POLICY_INVALID"
    assert pool.stats() == ZERO_STATS


def test_correct_secret_is_ok_and_charges_one_unit():
    pool = make_pool()
    make_policy(pool, budget_units=10)
    pool.add_record("t1", "alice", "s3cret", generation=1)
    result = pool.verify("t1", "alice", "s3cret", now=0)
    assert result == ph.VerifyResult(status="ok", reason="", retry_after_ms=0,
                                     needs_upgrade=False, generation=1)
    assert pool.budget_of("t1", now=0) == 9
    assert pool.stats()["verifies"] == 1
    assert pool.stats()["ok"] == 1
    assert pool.stats()["denied"] == 0


def test_wrong_secret_is_denied_and_counted_until_next_success():
    pool = make_pool()
    make_policy(pool)
    pool.add_record("t1", "alice", "right")
    result = pool.verify("t1", "alice", "wrong", now=0)
    assert (result.status, result.reason, result.needs_upgrade) == ("denied", "credentials", False)
    assert pool.failure_count("t1", "alice") == 1
    assert pool.stats()["denied"] == 1
    again = pool.verify("t1", "alice", "right", now=10)
    assert again.status == "ok"
    assert pool.failure_count("t1", "alice") == 0
    assert pool.stats()["ok"] == 1


def test_lockout_blocks_even_correct_secret_until_window_ends():
    pool = make_pool()
    make_policy(pool, lockout_threshold=3, lock_ms=60000)
    pool.add_record("t1", "bob", "right")
    for step in range(3):
        assert pool.verify("t1", "bob", "bad", now=step * 10).status == "denied"
    locked = pool.verify("t1", "bob", "right", now=100)
    assert (locked.status, locked.reason) == ("locked", "lockout")
    assert locked.retry_after_ms == 60020 - 100
    assert pool.stats()["locked"] == 1
    assert pool.verify("t1", "bob", "right", now=60020).status == "ok"
    assert pool.failure_count("t1", "bob") == 0


def test_unknown_account_is_denied_and_invalid_inputs_raise():
    pool = make_pool()
    make_policy(pool)
    result = pool.verify("t1", "ghost", "whatever", now=0)
    assert (result.status, result.reason, result.generation) == ("denied", "credentials", None)
    assert result.needs_upgrade is False
    assert pool.stats()["dummy"] == 1

    for tenant, account, secret in (("t1", "", "x"), ("t1", "   ", "x"), ("t1", "a", "")):
        with pytest.raises(ph.PasswordHashError) as exc:
            pool.verify(tenant, account, secret, now=10)
        assert exc.value.code == "HASH_INVALID_REQUEST"
    with pytest.raises(ph.PasswordHashError) as exc:
        pool.verify("missing", "alice", "x", now=10)
    assert exc.value.code == "HASH_UNKNOWN_TENANT"


def test_full_pool_returns_queue_timeout_with_predicted_wait():
    pool = make_pool(pool_capacity=1, queue_limit=4)
    make_policy(pool, cost_ms=40, queue_deadline_ms=20)
    pool.add_record("t1", "alice", "x")
    assert pool.verify("t1", "alice", "x", now=0).status == "ok"
    result = pool.verify("t1", "alice", "x", now=0)
    assert (result.status, result.reason) == ("retry", "queue_timeout")
    assert result.retry_after_ms == 40
    assert pool.stats()["queue_retries"] == 1
    assert pool.stats()["ok"] == 1
    assert pool.verify("t1", "alice", "x", now=40).status == "ok"


def test_waiting_queue_length_is_a_hard_cap():
    pool = make_pool(pool_capacity=1, queue_limit=1)
    make_policy(pool, cost_ms=40, queue_deadline_ms=1000, per_tenant_concurrency=8)
    pool.add_record("t1", "alice", "x")
    assert pool.verify("t1", "alice", "x", now=0).status == "ok"
    assert pool.verify("t1", "alice", "x", now=0).status == "ok"
    blocked = pool.verify("t1", "alice", "x", now=0)
    assert (blocked.status, blocked.reason) == ("retry", "queue_full")
    assert blocked.retry_after_ms == 40
    assert pool.stats()["queue_retries"] == 1


def test_per_tenant_concurrency_cap_does_not_block_other_tenants():
    pool = make_pool(pool_capacity=4, queue_limit=4)
    make_policy(pool, tenant="t1", per_tenant_concurrency=1, cost_ms=40,
                queue_deadline_ms=200)
    make_policy(pool, tenant="t2", per_tenant_concurrency=1, cost_ms=40,
                queue_deadline_ms=200)
    pool.add_record("t1", "a", "x")
    pool.add_record("t2", "b", "y")
    assert pool.verify("t1", "a", "x", now=0).status == "ok"
    blocked = pool.verify("t1", "a", "x", now=0)
    assert (blocked.status, blocked.reason) == ("retry", "concurrency")
    assert blocked.retry_after_ms == 40
    assert pool.stats()["queue_retries"] == 1
    assert pool.verify("t2", "b", "y", now=0).status == "ok"
    assert pool.verify("t1", "a", "x", now=40).status == "ok"


def test_concurrent_verifies_charge_exact_budget_and_counters():
    pool = make_pool(pool_capacity=32, queue_limit=32)
    make_policy(pool, budget_units=100, cost_ms=1, per_tenant_concurrency=32,
                queue_deadline_ms=1000)
    pool.add_record("t1", "alice", "x")
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            assert pool.verify("t1", "alice", "x", now=0).status == "ok"
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert pool.stats()["verifies"] == 8
    assert pool.stats()["ok"] == 8
    assert pool.budget_of("t1", now=0) == 92


def test_defer_upgrade_is_idempotent_and_ignores_budget():
    pool = make_pool()
    make_policy(pool, budget_units=1)
    pool.add_record("t1", "alice", "x", generation=1)
    pool.defer_upgrade("t1", "alice", generation=2)
    pool.defer_upgrade("t1", "alice", generation=2)
    pool.defer_upgrade("t1", "alice", generation=2)
    assert pool.pending_upgrades("t1") == [("alice", 2)]
    assert pool.stats()["deferred"] == 1
    with pytest.raises(ph.PasswordHashError) as exc:
        pool.defer_upgrade("t1", "  ", generation=2)
    assert exc.value.code == "HASH_INVALID_REQUEST"
    with pytest.raises(ph.PasswordHashError) as exc:
        pool.defer_upgrade("t1", "alice", generation=0)
    assert exc.value.code == "HASH_INVALID_REQUEST"


def test_run_upgrades_respects_limit_and_charges_upgrade_units():
    pool = make_pool()
    make_policy(pool, budget_units=20, upgrade_units=4)
    for name in ("carl", "alice", "bob"):
        pool.add_record("t1", name, "x", generation=1)
    assert pool.advance_generation("t1") == 2
    for name in ("carl", "alice", "bob"):
        stale = pool.verify("t1", name, "x", now=0)
        assert (stale.status, stale.needs_upgrade, stale.generation) == ("ok", True, 1)
    assert pool.pending_upgrades("t1") == [("alice", 2), ("bob", 2), ("carl", 2)]
    assert pool.stats()["deferred"] == 3

    first = pool.run_upgrades("t1", now=0, limit=1)
    assert [(row.account, row.status) for row in first] == [("alice", "upgraded")]
    assert (first[0].from_generation, first[0].to_generation) == (1, 2)
    assert len(pool.pending_upgrades("t1")) == 2
    assert pool.budget_of("t1", now=0) == 20 - 3 - 4

    second = pool.run_upgrades("t1", now=10)
    assert [row.account for row in second] == ["bob", "carl"]
    assert pool.pending_upgrades("t1") == []
    assert pool.stats()["upgrades"] == 3
    assert pool.verify("t1", "alice", "x", now=200).needs_upgrade is False


def test_unsupported_upgrade_fails_without_touching_verify_result():
    pool = make_pool()
    make_policy(pool, budget_units=6, upgrade_units=9)
    pool.add_record("t1", "alice", "x", generation=1)
    pool.advance_generation("t1")
    result = pool.verify("t1", "alice", "x", now=0)
    assert (result.status, result.needs_upgrade) == ("ok", True)

    outcomes = pool.run_upgrades("t1", now=0)
    assert len(outcomes) == 1
    assert (outcomes[0].status, outcomes[0].code) == ("failed", "HASH_UPGRADE_UNSUPPORTED")
    assert pool.stats()["upgrades_failed"] == 1
    assert pool.pending_upgrades("t1") == []
    after = pool.verify("t1", "alice", "x", now=10)
    assert (after.status, after.needs_upgrade) == ("ok", True)


def test_budget_window_rolls_over_and_exhaustion_is_retryable():
    pool = make_pool()
    make_policy(pool, budget_units=2, window_ms=1000, cost_ms=5, queue_deadline_ms=100)
    pool.add_record("t1", "alice", "x")
    assert pool.verify("t1", "alice", "x", now=0).status == "ok"
    assert pool.budget_of("t1", now=0) == 1
    assert pool.verify("t1", "alice", "x", now=10).status == "ok"
    assert pool.budget_of("t1", now=10) == 0

    blocked = pool.verify("t1", "alice", "x", now=20)
    assert (blocked.status, blocked.reason) == ("retry", "budget")
    assert blocked.retry_after_ms == 980
    assert pool.stats()["budget_retries"] == 1
    assert pool.budget_of("t1", now=1000) == 2
    assert pool.verify("t1", "alice", "x", now=1000).status == "ok"


def test_snapshot_is_detached_and_restore_rebuilds_every_layer():
    pool = make_pool(pool_capacity=2, queue_limit=2)
    make_policy(pool, budget_units=10, upgrade_units=4)
    pool.add_record("t1", "alice", "x", generation=1)
    pool.advance_generation("t1")
    assert pool.verify("t1", "alice", "x", now=0).needs_upgrade is True

    state = pool.snapshot()
    assert set(state) == {"version", "policies", "records", "generations",
                          "budget", "slots", "waiting", "failures", "locks",
                          "stats"}
    pristine = copy.deepcopy(state)
    state["records"].clear()
    state["stats"]["verifies"] = 999
    assert pool.stats()["verifies"] == 1
    assert pool.pending_upgrades("t1") == [("alice", 2)]

    clone = make_pool(pool_capacity=2, queue_limit=2)
    clone.restore(pristine)
    assert clone.stats()["verifies"] == 1
    assert clone.budget_of("t1", now=0) == 9
    assert clone.pending_upgrades("t1") == [("alice", 2)]
    assert clone.verify("t1", "alice", "x", now=10).status == "ok"
    assert clone.stats()["upgrades"] == 0


def test_restore_rejects_malformed_state_and_keeps_live_data():
    pool = make_pool()
    make_policy(pool)
    pool.add_record("t1", "alice", "x")
    assert pool.verify("t1", "alice", "x", now=0).status == "ok"
    good = pool.snapshot()
    without_version = {key: value for key, value in good.items() if key != "version"}

    bad_states = [
        None,
        {},
        {"version": 2, **without_version},
        {**good, "policies": "nope"},
        {**good, "records": {}},
        {**good, "stats": {"verifies": 1}},
        {**good, "slots": [["t1"]]},
        {**good, "budget": []},
    ]
    for bad in bad_states:
        with pytest.raises(ph.PasswordHashError) as exc:
            pool.restore(bad)
        assert exc.value.code == "HASH_SNAPSHOT_INVALID"

    assert pool.stats()["verifies"] == 1
    assert pool.verify("t1", "alice", "x", now=10).status == "ok"
