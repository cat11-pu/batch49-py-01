import copy
import threading

import pytest

import layered_lockout_ladder as ll

ACCOUNT = "alice"
SOURCE = "203.0.113.0/24"
DEVICE = "dev-01"


def ladder(**kwargs):
    return ll.LayeredLockoutLadder(**kwargs)


def fail(step, *, account=ACCOUNT, source=SOURCE, device=DEVICE):
    return step.record_failure(account, source, device)


def test_initial_state_is_unlocked_with_empty_stats():
    step = ladder()
    status = step.lock_state(ACCOUNT, SOURCE, DEVICE)
    assert status.locked is False
    assert (status.dimension, status.until, status.exempt) == (None, None, False)
    assert status.counts == ll.Counts(0, 0, 0)
    assert step.stats() == {"failures": 0, "successes": 0, "locks": 0,
                            "unlocks": 0, "exemptions": 0, "appointments": 0}
    assert step.tick() == 1


def test_account_locks_on_third_failure():
    step = ladder()
    first = fail(step)
    second = fail(step)
    third = fail(step)
    assert first.locked is False
    assert second.locked is False
    assert (third.locked, third.dimension, third.until, third.tier) == (
        True, "account", 2, 0)
    assert third.counts == ll.Counts(3, 3, 3)
    status = step.lock_state(ACCOUNT, SOURCE, DEVICE)
    assert (status.locked, status.dimension, status.until) == (True,
                                                               "account", 2)
    assert step.stats()["locks"] == 1


def test_second_tier_extends_the_lock():
    step = ladder()
    outcomes = [fail(step) for _ in range(5)]
    assert outcomes[2].until == 2
    assert (outcomes[4].locked, outcomes[4].until, outcomes[4].tier) == (
        True, 6, 1)
    assert step.stats()["locks"] == 1
    assert step.stats()["failures"] == 5


def test_success_clears_account_counter_only():
    step = ladder()
    for _ in range(3):
        fail(step)
    status = step.record_success(ACCOUNT, SOURCE, DEVICE)
    assert status.locked is False
    assert status.counts == ll.Counts(0, 3, 3)
    again = fail(step)
    assert (again.locked, again.counts.account) == (False, 1)
    assert step.stats()["successes"] == 1


def test_tick_auto_unlocks_expired_lock():
    step = ladder()
    for _ in range(3):
        fail(step)
    assert step.tick() == 1
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).locked is True
    assert step.tick() == 2
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).locked is False
    assert step.stats()["unlocks"] == 1


def test_scheduled_unlock_applies_at_its_tick():
    step = ladder()
    for _ in range(3):
        fail(step)
    appointment = step.schedule_unlock(ACCOUNT, ticks=1)
    assert appointment == ll.Appointment(ACCOUNT, 1)
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).until == 2
    assert step.tick() == 1
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).locked is False
    assert step.stats()["appointments"] == 1
    assert step.stats()["unlocks"] == 1


def test_schedule_unlock_rejects_bad_requests():
    step = ladder(max_appointments=1)
    with pytest.raises(ll.LockoutError) as exc:
        step.schedule_unlock(ACCOUNT, ticks=1)
    assert exc.value.code == "LOCKOUT_NOT_LOCKED"
    for _ in range(3):
        fail(step)
    with pytest.raises(ll.LockoutError) as exc:
        step.schedule_unlock(ACCOUNT, ticks=0)
    assert exc.value.code == "LOCKOUT_INVALID"
    with pytest.raises(ll.LockoutError) as exc:
        step.schedule_unlock(ACCOUNT, ticks=-2)
    assert exc.value.code == "LOCKOUT_INVALID"
    step.schedule_unlock(ACCOUNT, ticks=5)
    with pytest.raises(ll.LockoutError) as exc:
        step.schedule_unlock(ACCOUNT, ticks=6)
    assert exc.value.code == "LOCKOUT_APPOINTMENT_DUPLICATE"
    for _ in range(3):
        fail(step, account="bob", source="198.51.100.7", device="dev-02")
    with pytest.raises(ll.LockoutError) as exc:
        step.schedule_unlock("bob", ticks=5)
    assert exc.value.code == "LOCKOUT_QUEUE_FULL"
    assert step.stats()["appointments"] == 1


def test_grant_exemption_releases_the_account():
    step = ladder()
    for _ in range(3):
        fail(step)
    exemption = step.grant_exemption(ACCOUNT, reason="  oncall approved  ",
                                     ticks=5)
    assert exemption == ll.Exemption(ACCOUNT, "oncall approved", 5, 2)
    status = step.lock_state(ACCOUNT, SOURCE, DEVICE)
    assert (status.locked, status.exempt) == (False, True)
    assert step.stats()["exemptions"] == 1
    with pytest.raises(ll.LockoutError) as exc:
        step.grant_exemption(ACCOUNT, reason="again", ticks=1)
    assert exc.value.code == "LOCKOUT_NOT_LOCKED"


def test_exemption_requires_reason_and_positive_ticks():
    step = ladder()
    for _ in range(3):
        fail(step)
    with pytest.raises(ll.LockoutError) as exc:
        step.grant_exemption(ACCOUNT, reason="   ", ticks=2)
    assert exc.value.code == "LOCKOUT_INVALID"
    with pytest.raises(ll.LockoutError) as exc:
        step.grant_exemption(ACCOUNT, reason="ok", ticks=0)
    assert exc.value.code == "LOCKOUT_INVALID"
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).locked is True
    exemption = step.grant_exemption(ACCOUNT, reason="ok", ticks=4)
    assert exemption.expires_at == 4


def test_decay_restarts_the_account_counter():
    step = ladder(decay_after=5)
    fail(step)
    assert step.tick(5) == 5
    outcome = fail(step)
    assert (outcome.locked, outcome.counts.account) == (False, 1)
    assert step.stats()["failures"] == 2


def test_stats_track_each_operation():
    step = ladder()
    for _ in range(3):
        fail(step)
    step.record_success(ACCOUNT, SOURCE, DEVICE)
    for _ in range(3):
        fail(step, account="bob", source="198.51.100.9", device="dev-02")
    step.schedule_unlock("bob", ticks=5)
    assert step.tick(2) == 2
    assert step.stats() == {"failures": 6, "successes": 1, "locks": 2,
                            "unlocks": 1, "exemptions": 0, "appointments": 1}


def test_snapshot_is_detached_and_restore_roundtrips():
    step = ladder()
    for _ in range(3):
        fail(step)
    step.tick(1)
    step.schedule_unlock(ACCOUNT, ticks=3)
    state = step.snapshot()
    assert set(state) == {"version", "clock", "index", "locks", "exemptions",
                          "appointments", "stats"}
    pristine = copy.deepcopy(state)
    state["index"]["account"].clear()
    state["stats"]["failures"] = 99
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).locked is True
    assert step.stats()["failures"] == 3
    clone = ladder()
    clone.restore(pristine)
    assert clone.stats()["failures"] == 3
    assert clone.lock_state(ACCOUNT, SOURCE, DEVICE).locked is True
    assert clone.tick(3) == 4
    status = clone.lock_state(ACCOUNT, SOURCE, DEVICE)
    assert status.locked is False
    assert status.counts == ll.Counts(3, 3, 3)
    assert clone.stats()["unlocks"] == 1


def test_restore_rejects_malformed_snapshot():
    step = ladder()
    fail(step)
    good = step.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, index={"account": {}, "source": {}}),
        dict(good, locks=[]),
        dict(good, appointments={}),
        dict(good, stats={}),
    ]
    for bad in bad_states:
        with pytest.raises(ll.LockoutError) as exc:
            step.restore(bad)
        assert exc.value.code == "LOCKOUT_SNAPSHOT_INVALID"
    assert step.lock_state(ACCOUNT, SOURCE, DEVICE).counts.account == 1
    assert fail(step).counts.account == 2


def test_dimension_index_capacity_is_enforced():
    step = ladder(max_subjects=2)
    step.record_failure("u1", "s1", "d1")
    step.record_failure("u2", "s2", "d2")
    with pytest.raises(ll.LockoutError) as exc:
        step.record_failure("u3", "s1", "d1")
    assert exc.value.code == "LOCKOUT_INDEX_FULL"
    assert step.stats()["failures"] == 2


def test_concurrent_failures_are_counted_once_each():
    step = ladder()
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=5)
        try:
            fail(step)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    status = step.lock_state(ACCOUNT, SOURCE, DEVICE)
    assert status.counts == ll.Counts(8, 8, 8)
    assert (status.locked, status.dimension, status.until) == (True,
                                                               "account", 12)
    assert step.stats()["failures"] == 8
    assert step.stats()["locks"] == 1
