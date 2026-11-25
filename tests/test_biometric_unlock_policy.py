import copy
import threading

import pytest

import biometric_unlock_policy as bp


def policy(**kwargs):
    return bp.BiometricUnlockPolicy(**kwargs)


def enrolled(**kwargs):
    pol = policy(**kwargs)
    pol.enroll("phone-1", "tpl-a", now=0)
    return pol


def test_enroll_creates_generation_one_and_default_policy():
    pol = policy()
    record = pol.enroll("phone-1", "tpl-a", now=3)
    assert record.template_id == "tpl-a"
    assert record.device == "phone-1"
    assert record.generation == 1
    assert record.enrolled_at == 3
    assert record.status == "active"
    assert [row.template_id for row in pol.list_templates("phone-1")] == ["tpl-a"]
    assert pol.list_templates("other") == []
    device = pol.device_policy("phone-1")
    assert (device.biometric_enabled, device.liveness_threshold,
            device.password_fallback_limit) == (True, 0.80, 2)
    assert pol.stats() == {"enrollments": 1, "upgrades": 0, "unlocks": 0,
                           "fallbacks": 0, "failures": 0, "cooldowns": 0,
                           "password_fallbacks": 0}
    with pytest.raises(bp.BiometricError) as exc:
        pol.device_policy("ghost")
    assert exc.value.code == "BIO_DEVICE_UNKNOWN"


def test_duplicate_template_capacity_and_blank_id():
    pol = policy(max_templates=2)
    pol.enroll("phone-1", "tpl-a", now=0)
    with pytest.raises(bp.BiometricError) as exc:
        pol.enroll("phone-1", "tpl-a", now=1)
    assert exc.value.code == "BIO_TEMPLATE_DUPLICATE"
    pol.enroll("phone-1", "tpl-b", now=2)
    with pytest.raises(bp.BiometricError) as exc:
        pol.enroll("phone-1", "tpl-c", now=3)
    assert exc.value.code == "BIO_TEMPLATE_CAPACITY"
    with pytest.raises(bp.BiometricError) as exc:
        pol.enroll("phone-1", "   ", now=4)
    assert exc.value.code == "BIO_INVALID_REQUEST"
    assert pol.stats()["enrollments"] == 2


def test_unlock_above_threshold_reports_success():
    pol = enrolled()
    result = pol.unlock("phone-1", "tpl-a", 0.91, now=1)
    assert (result.status, result.reason, result.generation) == (
        "unlocked", "liveness_ok", 1)
    assert (result.failures, result.cooldown_until) == (0, None)
    assert pol.stats()["unlocks"] == 1
    assert pol.stats()["failures"] == 0


def test_unlock_below_threshold_requests_fallback():
    pol = enrolled()
    result = pol.unlock("phone-1", "tpl-a", 0.50, now=1)
    assert (result.status, result.reason, result.generation) == (
        "fallback_required", "liveness_low", 1)
    assert result.failures == 1
    assert pol.stats()["fallbacks"] == 1
    assert pol.stats()["failures"] == 1


def test_repeated_failures_enter_cooldown():
    pol = enrolled(failure_limit=2, cooldown=5)
    first = pol.unlock("phone-1", "tpl-a", 0.50, now=1)
    second = pol.unlock("phone-1", "tpl-a", 0.50, now=2)
    assert first.status == "fallback_required"
    assert first.failures == 1
    assert (second.status, second.failures, second.cooldown_until) == (
        "cooldown", 2, 7)
    assert pol.stats()["cooldowns"] == 1
    assert pol.stats()["failures"] == 2


def test_unlock_during_cooldown_short_circuits():
    pol = enrolled(failure_limit=2, cooldown=5)
    pol.unlock("phone-1", "tpl-a", 0.50, now=1)
    pol.unlock("phone-1", "tpl-a", 0.50, now=2)
    result = pol.unlock("phone-1", "tpl-a", 0.99, now=6)
    assert (result.status, result.cooldown_until) == ("cooldown", 7)
    assert result.failures == 2
    assert pol.stats()["unlocks"] == 0
    assert pol.stats()["failures"] == 2


def test_cooldown_expiry_allows_unlock_again():
    pol = enrolled(failure_limit=2, cooldown=5)
    pol.unlock("phone-1", "tpl-a", 0.50, now=1)
    pol.unlock("phone-1", "tpl-a", 0.50, now=2)
    result = pol.unlock("phone-1", "tpl-a", 0.99, now=7)
    assert result.status == "unlocked"
    assert result.failures == 0
    assert pol.stats()["unlocks"] == 1


def test_upgrade_applies_threshold_only_to_new_generation():
    pol = policy()
    pol.enroll("phone-1", "tpl-a", now=0)
    assert pol.upgrade_template("phone-1", liveness_threshold=0.95, now=1) == 2
    record = pol.enroll("phone-1", "tpl-b", now=2)
    assert record.generation == 2
    old = pol.unlock("phone-1", "tpl-a", 0.90, now=3)
    new = pol.unlock("phone-1", "tpl-b", 0.90, now=4)
    assert old.status == "unlocked"
    assert (new.status, new.generation, new.failures) == (
        "fallback_required", 2, 1)
    assert pol.stats()["upgrades"] == 1


def test_device_policy_threshold_is_a_floor():
    pol = policy()
    pol.enroll("phone-1", "tpl-a", liveness_threshold=0.90, now=0)
    device = pol.set_device_policy("phone-1", liveness_threshold=0.95)
    assert device.liveness_threshold == 0.95
    strict = pol.unlock("phone-1", "tpl-a", 0.92, now=1)
    assert strict.status == "fallback_required"
    pol.set_device_policy("phone-1", liveness_threshold=0.60)
    relaxed = pol.unlock("phone-1", "tpl-a", 0.92, now=2)
    assert relaxed.status == "unlocked"
    with pytest.raises(bp.BiometricError) as exc:
        pol.set_device_policy("ghost", liveness_threshold=0.5)
    assert exc.value.code == "BIO_DEVICE_UNKNOWN"


def test_password_fallback_success_resets_failures():
    pol = enrolled()
    pol.unlock("phone-1", "tpl-a", 0.40, now=1)
    result = pol.fallback_password("phone-1", True, now=2)
    assert (result.status, result.failures) == ("unlocked", 0)
    assert pol.stats()["password_fallbacks"] == 1
    assert pol.stats()["failures"] == 1


def test_password_fallback_rejection_keeps_biometric_counter():
    pol = enrolled()
    result = pol.fallback_password("phone-1", False, now=1)
    assert result.status == "password_rejected"
    assert pol.stats()["password_fallbacks"] == 1
    assert pol.stats()["failures"] == 0
    with pytest.raises(bp.BiometricError) as exc:
        pol.fallback_password("ghost", True)
    assert exc.value.code == "BIO_DEVICE_UNKNOWN"


def test_stats_track_each_operation():
    pol = policy()
    pol.enroll("phone-1", "tpl-a", now=0)
    pol.enroll("phone-2", "tpl-b", now=1)
    pol.unlock("phone-1", "tpl-a", 0.90, now=2)
    pol.unlock("phone-2", "tpl-b", 0.10, now=3)
    pol.upgrade_template("phone-1", liveness_threshold=0.90, now=4)
    pol.fallback_password("phone-2", True, now=5)
    assert pol.stats() == {"enrollments": 2, "upgrades": 1, "unlocks": 1,
                           "fallbacks": 1, "failures": 1, "cooldowns": 0,
                           "password_fallbacks": 1}


def test_snapshot_is_detached_and_restore_roundtrips():
    pol = enrolled()
    pol.unlock("phone-1", "tpl-a", 0.20, now=1)
    state = pol.snapshot()
    assert set(state) == {"version", "devices", "templates", "thresholds",
                          "counters"}
    pristine = copy.deepcopy(state)
    state["templates"].clear()
    state["counters"]["unlocks"] = 77
    assert len(pol.list_templates("phone-1")) == 1
    assert pol.stats()["unlocks"] == 0
    clone = policy()
    clone.restore(pristine)
    assert [row.template_id for row in clone.list_templates("phone-1")] == ["tpl-a"]
    assert clone.device_policy("phone-1").liveness_threshold == 0.80
    assert clone.stats()["failures"] == 1
    again = clone.unlock("phone-1", "tpl-a", 0.20, now=10)
    assert again.failures == 2


def test_restore_rejects_malformed_snapshot():
    pol = enrolled()
    good = pol.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, devices="nope"),
        dict(good, templates={"tpl-a": 1}),
        dict(good, counters={}),
    ]
    for bad in bad_states:
        with pytest.raises(bp.BiometricError) as exc:
            pol.restore(bad)
        assert exc.value.code == "BIO_SNAPSHOT_INVALID"
    assert len(pol.list_templates("phone-1")) == 1
    assert pol.unlock("phone-1", "tpl-a", 0.90, now=2).status == "unlocked"


def test_concurrent_enroll_of_same_template_yields_one_record():
    pol = policy()
    outcomes = []
    barrier = threading.Barrier(8)

    def worker(index):
        barrier.wait(timeout=5)
        try:
            pol.enroll("phone-1", "tpl-race", now=index)
            outcomes.append("created")
        except bp.BiometricError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # pragma: no cover - reported via outcomes
            outcomes.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,))
               for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("created") == 1
    assert outcomes.count("BIO_TEMPLATE_DUPLICATE") == 7
    assert len(pol.list_templates("phone-1")) == 1
    assert pol.stats()["enrollments"] == 1
