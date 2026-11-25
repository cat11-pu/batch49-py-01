import copy

import pytest

import trusted_device_ladder as td

NOW = 1000
IDLE = 1000


def ladder(**kwargs):
    return td.TrustedDeviceLadder(**kwargs)


def reg(owner, device_id, stable_key=None, *, user="u1", now=NOW):
    return owner.register_device(user=user, device_id=device_id,
                                 stable_key=stable_key or device_id, now=now)


def login(owner, device_id, *, user="u1", success=True,
          method="password", now=NOW + 1):
    return owner.record_login(user=user, device_id=device_id,
                              success=success, method=method, now=now)


def promote_to(owner, device_id, level, *, start=NOW + 1, user="u1"):
    """Drive a device up the ladder using successful logins."""
    moment = start
    while True:
        record = [row for row in owner.list_devices(user)
                  if row.device_id == device_id][0]
        if record.level >= level:
            return record
        if not record.verified:
            login(owner, device_id, user=user, method="mfa", now=moment)
            moment += 1
            continue
        threshold = {0: 2, 1: 3, 2: 4}[record.level]
        for _ in range(threshold):
            login(owner, device_id, user=user, method="password", now=moment)
            moment += 1
        owner.promote(user=user, device_id=device_id, now=moment)
        moment += 1


def test_register_device_starts_at_untrusted_level():
    owner = ladder()
    record = reg(owner, "d1")
    assert record.user == "u1"
    assert record.device_id == "d1"
    assert record.stable_key == "d1"
    assert record.family_id == "solo:d1"
    assert record.level == 0
    assert record.consecutive == 0
    assert record.verified is False
    assert record.last_used == NOW
    assert record.revoked is False
    assert [row.device_id for row in owner.list_devices("u1")] == ["d1"]
    assert owner.stats()["registered"] == 1


def test_duplicate_device_and_blank_identity_are_rejected():
    owner = ladder()
    reg(owner, "d1")
    with pytest.raises(td.TrustedDeviceError) as exc:
        reg(owner, "d1")
    assert exc.value.code == "DEVICE_DUPLICATE"
    with pytest.raises(td.TrustedDeviceError) as exc:
        reg(owner, "", stable_key="k")
    assert exc.value.code == "DEVICE_INVALID_REQUEST"
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.register_device(user="   ", device_id="d2", stable_key="k2",
                              now=NOW)
    assert exc.value.code == "DEVICE_INVALID_REQUEST"
    assert [row.device_id for row in owner.list_devices("u1")] == ["d1"]


def test_successful_logins_accumulate_and_mfa_sets_verified():
    owner = ladder()
    reg(owner, "d1")
    first = login(owner, "d1", now=NOW + 1)
    assert first.consecutive == 1
    assert first.last_used == NOW + 1
    assert first.verified is False
    second = login(owner, "d1", method="mfa", now=NOW + 2)
    assert second.consecutive == 2
    assert second.last_used == NOW + 2
    assert second.verified is True
    with pytest.raises(td.TrustedDeviceError) as exc:
        login(owner, "d9", now=NOW + 3)
    assert exc.value.code == "DEVICE_NOT_FOUND"


def test_promotion_needs_two_consecutive_successes():
    owner = ladder()
    reg(owner, "d1")
    login(owner, "d1", now=NOW + 1)
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.promote(user="u1", device_id="d1", now=NOW + 2)
    assert exc.value.code == "DEVICE_PROMOTION_NOT_ELIGIBLE"

    login(owner, "d1", now=NOW + 2)
    promoted = owner.promote(user="u1", device_id="d1", now=NOW + 3)
    assert promoted.level == 1
    assert promoted.consecutive == 0
    assert owner.stats()["promoted"] == 1
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.promote(user="u1", device_id="d1", now=NOW + 4)
    assert exc.value.code == "DEVICE_PROMOTION_NOT_ELIGIBLE"


def test_second_level_requires_a_verified_factor():
    owner = ladder()
    reg(owner, "d1")
    login(owner, "d1", now=NOW + 1)
    login(owner, "d1", now=NOW + 2)
    owner.promote(user="u1", device_id="d1", now=NOW + 3)
    for offset in range(3):
        login(owner, "d1", now=NOW + 10 + offset)
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.promote(user="u1", device_id="d1", now=NOW + 20)
    assert exc.value.code == "DEVICE_PROMOTION_NOT_ELIGIBLE"

    login(owner, "d1", method="mfa", now=NOW + 21)
    promoted = owner.promote(user="u1", device_id="d1", now=NOW + 22)
    assert promoted.level == 2
    assert promoted.verified is True


def test_demote_steps_down_one_level_and_resets_progress():
    owner = ladder()
    reg(owner, "d1")
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.demote(user="u1", device_id="d1", now=NOW + 1)
    assert exc.value.code == "DEVICE_ALREADY_LOWEST"

    promote_to(owner, "d1", 1)
    stepped = owner.demote(user="u1", device_id="d1", now=NOW + 20)
    assert stepped.level == 0
    assert stepped.consecutive == 0
    assert owner.stats()["demoted"] == 1


def test_tick_demotes_idle_devices_by_one_level():
    owner = ladder(idle_seconds=IDLE)
    reg(owner, "d1", now=NOW)
    reg(owner, "d2", now=NOW)
    promote_to(owner, "d1", 1, start=NOW + 1)
    idle_start = [row for row in owner.list_devices("u1")
                  if row.device_id == "d1"][0].last_used

    assert owner.tick(idle_start + IDLE - 1) == 0
    assert owner.tick(idle_start + IDLE) == 1
    rows = {row.device_id: row for row in owner.list_devices("u1")}
    assert rows["d1"].level == 0
    assert rows["d2"].level == 0
    assert owner.tick(idle_start + IDLE + 1) == 0


def test_user_cap_evicts_lowest_and_least_recently_used():
    owner = ladder(max_devices_per_user=2)
    reg(owner, "d1", now=NOW)
    reg(owner, "d2", now=NOW + 1)
    promote_to(owner, "d2", 1, start=NOW + 2)

    reg(owner, "d3", now=NOW + 40)
    assert [row.device_id for row in owner.list_devices("u1")] == ["d2", "d3"]
    assert owner.stats()["evicted"] == 1
    assert owner.stats()["active_devices"] == 2


def test_family_member_cap_blocks_registration():
    owner = ladder(max_family_members=2)
    reg(owner, "d1", stable_key="k")
    reg(owner, "d2", stable_key="k")
    with pytest.raises(td.TrustedDeviceError) as exc:
        reg(owner, "d3", stable_key="k")
    assert exc.value.code == "DEVICE_FAMILY_CAPACITY"
    assert len(owner.list_devices("u1")) == 2


def test_merge_family_keeps_the_strongest_level():
    owner = ladder()
    reg(owner, "d1", stable_key="k")
    reg(owner, "d2", stable_key="k")
    promote_to(owner, "d2", 2, start=NOW + 1)

    merged = owner.merge_family(user="u1", stable_key="k", now=NOW + 40)
    assert merged.family_id == "family:k"
    assert merged.stable_key == "k"
    assert merged.members == ("d1", "d2")
    assert merged.level == 2

    rows = {row.device_id: row for row in owner.list_devices("u1")}
    assert rows["d1"].family_id == rows["d2"].family_id == "family:k"
    assert rows["d1"].level == rows["d2"].level == 2
    assert owner.stats()["merged"] == 2


def test_revoke_removes_device_and_is_not_repeatable():
    owner = ladder()
    reg(owner, "d1")
    reg(owner, "d2")
    revoked = owner.revoke(user="u1", device_id="d1", now=NOW + 1)
    assert revoked == ("d1",)
    assert [row.device_id for row in owner.list_devices("u1")] == ["d2"]
    assert owner.stats()["revoked"] == 1
    assert owner.stats()["active_devices"] == 1
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.revoke(user="u1", device_id="d1", now=NOW + 2)
    assert exc.value.code == "DEVICE_NOT_FOUND"


def test_list_devices_is_sorted_and_user_scoped():
    owner = ladder()
    reg(owner, "d2", now=NOW)
    reg(owner, "d1", now=NOW + 1)
    reg(owner, "x1", user="u2", now=NOW + 2)
    assert [row.device_id for row in owner.list_devices("u1")] == ["d1", "d2"]
    assert [row.device_id for row in owner.list_devices("u2")] == ["x1"]
    assert owner.list_devices("ghost") == []


def test_snapshot_is_detached_and_restore_roundtrips():
    owner = ladder()
    reg(owner, "d1")
    promote_to(owner, "d1", 1, start=NOW + 1)
    state = owner.snapshot()
    assert set(state) == {"version", "devices", "stats"}
    pristine = copy.deepcopy(state)

    state["devices"].clear()
    state["stats"]["registered"] = 99
    assert len(owner.list_devices("u1")) == 1
    assert owner.stats()["registered"] == 1

    clone = ladder()
    clone.restore(pristine)
    restored = clone.list_devices("u1")
    assert len(restored) == 1
    assert restored[0].device_id == "d1"
    assert restored[0].level == 1
    assert clone.stats()["registered"] == 1
    assert clone.stats()["promoted"] == 1


def test_stats_track_register_promote_demote_revoke_exactly():
    owner = ladder(idle_seconds=IDLE)
    reg(owner, "d1", now=NOW)
    reg(owner, "d2", now=NOW + 1)
    promote_to(owner, "d1", 1, start=NOW + 2)
    owner.demote(user="u1", device_id="d1", now=NOW + 40)
    owner.tick(NOW + 200)
    owner.revoke(user="u1", device_id="d2", now=NOW + 300)

    assert owner.stats() == {"registered": 2, "promoted": 1, "demoted": 1,
                             "merged": 0, "revoked": 1, "evicted": 0,
                             "active_devices": 1, "families": 1}


def test_ladder_reaches_top_level_and_stops():
    owner = ladder()
    reg(owner, "d1")
    promote_to(owner, "d1", 3, start=NOW + 1)
    rows = owner.list_devices("u1")
    assert rows[0].level == 3
    with pytest.raises(td.TrustedDeviceError) as exc:
        owner.promote(user="u1", device_id="d1", now=NOW + 100)
    assert exc.value.code == "DEVICE_ALREADY_TOP"
