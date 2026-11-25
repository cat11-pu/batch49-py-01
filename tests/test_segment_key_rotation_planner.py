import copy
import threading

import pytest

import segment_key_rotation_planner as sk


def planner(**kwargs):
    return sk.SegmentKeyRotationPlanner(**kwargs)


def add_segments(p, count, duration_ms=2000):
    for index in range(count):
        p.add_segment(index, duration_ms)


def test_add_segment_assigns_epochs_by_period():
    p = planner()
    add_segments(p, 5)
    assert [p.epoch_for_segment(index) for index in range(5)] == [0, 0, 0, 1, 1]
    assert p.add_segment(5, 2000) == 1


def test_add_segment_requires_contiguous_index_and_positive_duration():
    p = planner()
    with pytest.raises(sk.KeyRotationError) as exc:
        p.add_segment(1, 2000)
    assert exc.value.code == "SEGMENT_OUT_OF_ORDER"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.add_segment(0, 0)
    assert exc.value.code == "SEGMENT_INVALID"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.add_segment(0, -5)
    assert exc.value.code == "SEGMENT_INVALID"
    assert p.add_segment(0, 2000) == 0
    with pytest.raises(sk.KeyRotationError) as exc:
        p.epoch_for_segment(4)
    assert exc.value.code == "SEGMENT_UNKNOWN"


def test_register_key_rejects_duplicate_and_invalid_epoch():
    p = planner()
    p.register_key("k0", 0)
    with pytest.raises(sk.KeyRotationError) as exc:
        p.register_key("k0", 1)
    assert exc.value.code == "KEY_DUPLICATE"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.register_key("", 0)
    assert exc.value.code == "KEY_INVALID"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.register_key("k1", -1)
    assert exc.value.code == "KEY_INVALID"
    p.register_key("k1", 0)
    assert p.key_count() == 2


def test_register_key_limit():
    p = planner(max_keys=2)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    with pytest.raises(sk.KeyRotationError) as exc:
        p.register_key("k2", 2)
    assert exc.value.code == "KEY_LIMIT"
    assert p.key_count() == 2


def test_rotate_activates_key_and_returns_previous():
    p = planner()
    add_segments(p, 5)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    assert p.rotate(0, "k0") is None
    assert p.active_key() == "k0"
    assert p.rotate(3, "k1") == "k0"
    assert p.active_key() == "k1"
    assert p.stats()["rotations"] == 2


def test_rotate_rejects_segment_not_on_period_boundary():
    p = planner()
    add_segments(p, 3)
    p.register_key("k0", 0)
    with pytest.raises(sk.KeyRotationError) as exc:
        p.rotate(1, "k0")
    assert exc.value.code == "ROTATION_NOT_AT_BOUNDARY"
    assert p.active_key() is None


def test_rotate_rejects_epoch_mismatch_and_unknown_key():
    p = planner()
    add_segments(p, 4)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    with pytest.raises(sk.KeyRotationError) as exc:
        p.rotate(3, "k0")
    assert exc.value.code == "KEY_EPOCH_MISMATCH"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.rotate(0, "ghost")
    assert exc.value.code == "KEY_UNKNOWN"


def test_repeated_same_key_rotation_is_idempotent():
    p = planner()
    add_segments(p, 2)
    p.register_key("k0", 0)
    p.rotate(0, "k0")
    p.rotate(0, "k0")
    assert p.active_key() == "k0"
    assert p.grace_window() is None
    assert p.stats()["rotations"] == 1


def test_rotate_conflicts_with_other_key_on_same_segment():
    p = planner()
    add_segments(p, 2)
    p.register_key("k0", 0)
    p.register_key("k1", 0)
    p.rotate(0, "k0")
    with pytest.raises(sk.KeyRotationError) as exc:
        p.rotate(0, "k1")
    assert exc.value.code == "ROTATION_CONFLICT"
    assert p.active_key() == "k0"
    assert p.stats()["rotations"] == 1


def test_grace_window_expires_and_revokes_old_key():
    p = planner(period_ms=2000, grace_segments=2)
    add_segments(p, 4)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    p.rotate(0, "k0")
    p.rotate(1, "k1")
    assert p.grace_window(2) == ("k0", "k1")
    p.add_segment(4, 2000)
    assert p.grace_window() is None
    assert p.is_revoked("k0") is True
    assert p.stats()["revocations"] == 1


def test_revocation_limit_blocks_extra_revoke():
    p = planner(period_ms=2000, grace_segments=0, max_revocations=1)
    add_segments(p, 3)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    p.rotate(0, "k0")
    p.rotate(1, "k1")
    assert p.is_revoked("k0") is True
    with pytest.raises(sk.KeyRotationError) as exc:
        p.revoke("k1")
    assert exc.value.code == "REVOCATION_LIMIT"
    assert p.stats()["revocations"] == 1


def test_license_expires_and_unknown_license_is_rejected():
    p = planner()
    add_segments(p, 5)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    p.rotate(0, "k0")
    p.rotate(3, "k1")
    license_id = p.license_for(3, ttl_segments=2)
    assert isinstance(license_id, str) and license_id
    assert p.license_usable(license_id, 4) is True
    assert p.license_usable(license_id, 5) is False
    with pytest.raises(sk.KeyRotationError) as exc:
        p.license_usable("lic-missing", 4)
    assert exc.value.code == "LICENSE_UNKNOWN"
    with pytest.raises(sk.KeyRotationError) as exc:
        p.license_for(9)
    assert exc.value.code == "SEGMENT_UNKNOWN"


def test_concurrent_rotation_has_single_winner():
    p = planner()
    add_segments(p, 2)
    p.register_key("k0", 0)
    p.register_key("k1", 0)
    barrier = threading.Barrier(2)
    outcomes = []

    def worker(key_id):
        try:
            barrier.wait(timeout=5)
            previous = p.rotate(0, key_id)
            outcomes.append(("ok", key_id, previous))
        except sk.KeyRotationError as exc:
            outcomes.append(("err", key_id, exc.code))

    threads = [threading.Thread(target=worker, args=(key,)) for key in ("k0", "k1")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    winners = [row for row in outcomes if row[0] == "ok"]
    losers = [row for row in outcomes if row[0] == "err"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0][2] == "ROTATION_CONFLICT"
    assert p.active_key() == winners[0][1]


def test_snapshot_is_detached_and_restores():
    p = planner(period_ms=2000, grace_segments=2)
    add_segments(p, 4)
    p.register_key("k0", 0)
    p.register_key("k1", 1)
    p.register_key("k2", 2)
    p.rotate(0, "k0")
    p.rotate(1, "k1")
    license_id = p.license_for(2, ttl_segments=2)
    snap = p.snapshot()
    assert set(snap) == {
        "version",
        "segments",
        "keys",
        "active",
        "grace",
        "revoked",
        "licenses",
        "counters",
    }
    state = copy.deepcopy(snap)
    clone = planner(period_ms=2000, grace_segments=2)
    clone.restore(state)
    assert clone.active_key() == p.active_key()
    assert clone.grace_window() == p.grace_window()
    assert clone.stats() == p.stats()
    assert clone.license_usable(license_id, 2) == p.license_usable(license_id, 2)
    snap["segments"].clear()
    assert p.stats()["segments"] == 4


def test_restore_rejects_malformed_state():
    p = planner(period_ms=2000)
    add_segments(p, 2)
    p.register_key("k0", 0)
    p.rotate(0, "k0")
    bad_states = [
        None,
        {},
        {"version": 1, "segments": [], "keys": {}, "active": "k0"},
        {
            "version": 1,
            "segments": "nope",
            "keys": {},
            "active": None,
            "grace": [],
            "revoked": [],
            "licenses": {},
            "counters": {},
        },
        {
            "version": 1,
            "segments": [],
            "keys": [],
            "active": None,
            "grace": [],
            "revoked": [],
            "licenses": {},
            "counters": {},
        },
    ]
    for state in bad_states:
        with pytest.raises(sk.KeyRotationError) as exc:
            p.restore(state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert p.active_key() == "k0"
    assert p.stats()["segments"] == 2
