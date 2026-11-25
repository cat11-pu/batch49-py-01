import copy
import threading

import pytest

import party_pity_isolator as pp


def test_register_members_assigns_order_and_rejects_duplicate():
    iso = pp.PartyPityIsolator()
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    state = iso.member_state("raid", "m-b")
    assert (state.party_id, state.member_id, state.order) == ("raid", "m-b", 1)
    assert (state.pity, state.kills, state.offline) == (0, 0, False)

    with pytest.raises(pp.PartyPityError) as exc:
        iso.register_member("raid", "m-b")
    assert exc.value.code == "PARTY_MEMBER_EXISTS"
    with pytest.raises(pp.PartyPityError) as exc:
        iso.register_member("  ", "m-c")
    assert exc.value.code == "PARTY_INVALID_REQUEST"


def test_record_kill_increments_every_member_counter():
    iso = pp.PartyPityIsolator(pity_threshold=10)
    for member in ("m-a", "m-b", "m-c"):
        iso.register_member("raid", member)
    drops = iso.record_kill("kill-1", "raid", now=10)
    assert [row.member_id for row in drops] == ["m-a", "m-b", "m-c"]
    assert [row.rarity for row in drops] == [None, None, None]
    assert [row.pity_after for row in drops] == [1, 1, 1]
    assert iso.stats()["kills"] == 1
    assert iso.stats()["drops"] == 0


def test_pity_triggers_at_threshold_and_resets_counter():
    iso = pp.PartyPityIsolator(pity_threshold=2)
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    first = iso.record_kill("kill-1", "raid", now=0)
    assert [row.rarity for row in first] == [None, None]
    second = iso.record_kill("kill-2", "raid", now=1)
    assert [row.rarity for row in second] == ["pity", "pity"]
    assert [row.pity_before for row in second] == [1, 1]
    assert [row.pity_after for row in second] == [0, 0]
    assert iso.stats()["pity_drops"] == 2


def test_one_member_pity_does_not_disturb_others():
    iso = pp.PartyPityIsolator(pity_threshold=3)
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    iso.record_kill("kill-1", "raid", now=0)
    iso.record_kill("kill-2", "raid", now=1)
    iso.reset_member("raid", "m-b")
    drops = iso.record_kill("kill-3", "raid", now=2)
    assert [(row.member_id, row.rarity) for row in drops] == [("m-a", "pity"),
                                                              ("m-b", None)]
    assert iso.member_state("raid", "m-a").pity == 0
    assert iso.member_state("raid", "m-b").pity == 1


def test_roll_for_returns_recorded_outcome():
    iso = pp.PartyPityIsolator()
    iso.register_member("raid", "m-a")
    iso.record_kill("kill-1", "raid", rolls={"m-a": 0}, now=0)
    drop = iso.roll_for("kill-1", "m-a")
    assert drop.rarity == "normal"
    assert (drop.pity_before, drop.pity_after) == (0, 1)
    assert iso.roll_for("kill-1", "ghost") is None


def test_bonus_rate_grows_with_party_and_caps():
    iso = pp.PartyPityIsolator(base_rate=200, per_member_bonus=50, max_rate=500)
    for index in range(10):
        iso.register_member("raid", f"m-{index}")
    assert iso.bonus_rate("raid") == 500

    small = pp.PartyPityIsolator()
    small.register_member("solo", "m-a")
    assert small.bonus_rate("solo") == 200
    small.register_member("solo", "m-b")
    small.register_member("solo", "m-c")
    assert small.bonus_rate("solo") == 300


def test_roll_boundary_uses_team_rate():
    iso = pp.PartyPityIsolator(pity_threshold=50)
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    drops = iso.record_kill("kill-1", "raid", rolls={"m-a": 249, "m-b": 250}, now=0)
    assert [row.rarity for row in drops] == ["normal", None]
    assert iso.stats()["normal_drops"] == 1


def test_record_kill_rejects_bad_rolls_and_unknown_party():
    iso = pp.PartyPityIsolator()
    iso.register_member("raid", "m-a")
    for bad_roll in (-1, 1000, "high"):
        with pytest.raises(pp.PartyPityError) as exc:
            iso.record_kill("kill-1", "raid", rolls={"m-a": bad_roll}, now=0)
        assert exc.value.code == "PARTY_INVALID_REQUEST"
    with pytest.raises(pp.PartyPityError) as exc:
        iso.record_kill("kill-2", "nope", now=0)
    assert exc.value.code == "PARTY_UNKNOWN"
    with pytest.raises(pp.PartyPityError) as exc:
        iso.record_kill("", "raid", now=0)
    assert exc.value.code == "PARTY_INVALID_REQUEST"
    assert iso.member_state("raid", "m-a").pity == 0


def test_counter_stops_at_hard_cap():
    iso = pp.PartyPityIsolator(pity_threshold=100, max_counter=3)
    iso.register_member("raid", "m-a")
    for index in range(6):
        iso.record_kill(f"kill-{index}", "raid", now=index)
    assert iso.member_state("raid", "m-a").pity == 3
    assert iso.stats()["pity_drops"] == 0


def test_reset_member_zeroes_counter_and_counts():
    iso = pp.PartyPityIsolator(pity_threshold=10)
    iso.register_member("raid", "m-a")
    iso.record_kill("kill-1", "raid", now=0)
    iso.record_kill("kill-2", "raid", now=1)
    assert iso.reset_member("raid", "m-a") == 2
    assert iso.member_state("raid", "m-a").pity == 0
    assert iso.stats()["resets"] == 1
    with pytest.raises(pp.PartyPityError) as exc:
        iso.reset_member("raid", "ghost")
    assert exc.value.code == "PARTY_UNKNOWN_MEMBER"


def test_member_state_reports_kills_and_order():
    iso = pp.PartyPityIsolator()
    iso.register_member("raid", "m-b")
    iso.register_member("raid", "m-a")
    iso.record_kill("kill-1", "raid", now=0)
    state = iso.member_state("raid", "m-b")
    assert (state.order, state.kills) == (0, 1)
    order = [row.member_id for row in iso.record_kill("kill-2", "raid", now=1)]
    assert order == ["m-b", "m-a"]


def test_snapshot_is_detached():
    iso = pp.PartyPityIsolator()
    iso.register_member("raid", "m-a")
    iso.record_kill("kill-1", "raid", now=0)
    state = iso.snapshot()
    assert set(state) == {"schema", "members", "kills", "stats"}
    state["members"].clear()
    state["stats"]["kills"] = 99
    assert iso.member_state("raid", "m-a").pity == 1
    assert iso.stats()["kills"] == 1


def test_restore_roundtrip_and_rejects_malformed():
    iso = pp.PartyPityIsolator(pity_threshold=5)
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    iso.record_kill("kill-1", "raid", rolls={"m-a": 0}, now=0)
    state = iso.snapshot()

    clone = pp.PartyPityIsolator(pity_threshold=5)
    clone.restore(copy.deepcopy(state))
    assert clone.member_state("raid", "m-b").pity == 1
    assert clone.roll_for("kill-1", "m-a").rarity == "normal"
    assert clone.stats()["kills"] == 1

    bad_states = [
        None,
        {},
        {"schema": 9, "members": [], "kills": {}, "stats": {}},
        {"schema": 1, "members": "nope", "kills": {}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(pp.PartyPityError) as exc:
            clone.restore(bad)
        assert exc.value.code == "PARTY_STATE_INVALID"
    assert clone.stats()["kills"] == 1


def test_concurrent_kills_share_party_counter_exactly():
    iso = pp.PartyPityIsolator(pity_threshold=1000, max_counter=1000)
    iso.register_member("raid", "m-a")
    iso.register_member("raid", "m-b")
    errors = []
    barrier = threading.Barrier(12)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            iso.record_kill(f"kill-{index:02d}", "raid", now=index)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert iso.member_state("raid", "m-a").pity == 12
    assert iso.member_state("raid", "m-b").pity == 12
    assert iso.stats()["kills"] == 12


def test_stats_track_drops_separately():
    iso = pp.PartyPityIsolator(pity_threshold=2)
    iso.register_member("raid", "m-a")
    iso.record_kill("kill-1", "raid", rolls={"m-a": 0}, now=0)
    iso.record_kill("kill-2", "raid", rolls={"m-a": 999}, now=1)
    stats = iso.stats()
    assert stats["kills"] == 2
    assert stats["normal_drops"] == 1
    assert stats["pity_drops"] == 1
    assert stats["drops"] == 2
