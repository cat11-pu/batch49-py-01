import copy

import pytest

import replay_integrity_guard as rg

HEX_DIGITS = set("0123456789abcdef")


def test_seal_first_segment_roots_the_chain():
    guard = rg.ReplayIntegrityGuard()
    digest = guard.seal_segment(0, [{"type": "damage"}, {"type": "heal"}], version=1)
    assert digest.index == 0
    assert digest.prev_digest == "0" * 64
    assert len(digest.digest) == 64
    assert set(digest.digest) <= HEX_DIGITS
    assert digest.event_count == 2
    assert digest.version == 1
    assert [row.index for row in guard.segments()] == [0]
    assert guard.stats()["sealed"] == 1


def test_second_segment_links_previous_digest():
    guard = rg.ReplayIntegrityGuard()
    first = guard.seal_segment(0, [{"type": "damage"}])
    second = guard.seal_segment(1, [{"type": "loot"}], version=2)
    assert second.prev_digest == first.digest
    assert second.digest != first.digest
    assert guard.segments()[1].prev_digest == first.digest


def test_seal_requires_contiguous_index():
    guard = rg.ReplayIntegrityGuard()
    with pytest.raises(rg.ReplayGuardError) as exc:
        guard.seal_segment(1, [{"type": "damage"}])
    assert exc.value.code == "REPLAY_SEGMENT_ORDER"
    guard.seal_segment(0, [{"type": "damage"}])
    with pytest.raises(rg.ReplayGuardError) as exc:
        guard.seal_segment(0, [{"type": "heal"}])
    assert exc.value.code == "REPLAY_SEGMENT_ORDER"


def test_seal_rejects_empty_or_non_mapping_events():
    guard = rg.ReplayIntegrityGuard()
    for events in ([], [{"type": "damage"}, "oops"], "nope"):
        with pytest.raises(rg.ReplayGuardError) as exc:
            guard.seal_segment(0, events)
        assert exc.value.code == "REPLAY_INVALID_REQUEST"
    assert guard.stats()["sealed"] == 0


def test_clean_replay_verifies():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage", "hp": 10}])
    guard.seal_segment(1, [{"type": "heal", "hp": 20}])
    assert guard.verify() is True
    assert guard.locate_tamper() is None
    assert guard.stats()["verified_ok"] == 1


def test_patch_last_segment_fails_verification_and_locates():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage", "hp": 10}])
    guard.seal_segment(1, [{"type": "damage", "hp": 20}])
    guard.patch_events(1, [{"type": "damage", "hp": 999}])
    assert guard.verify() is False
    assert guard.locate_tamper() == 1
    assert guard.stats()["verified_bad"] == 1
    assert guard.stats()["patched"] == 1


def test_locate_reports_earliest_patched_segment():
    guard = rg.ReplayIntegrityGuard()
    for index in range(4):
        guard.seal_segment(index, [{"type": "damage", "n": index}])
    guard.patch_events(2, [{"type": "damage", "n": 22}])
    guard.patch_events(3, [{"type": "damage", "n": 33}])
    assert guard.locate_tamper() == 2


def test_export_prefix_flattens_segments_in_order():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage", "hp": 10},
                           {"type": "heal", "hp": 30}], version=1)
    guard.seal_segment(1, [{"type": "loot", "amt": 7}], version=2)
    rows = guard.export_prefix()
    assert [row["seq"] for row in rows] == [1, 2, 3]
    assert [row["segment"] for row in rows] == [0, 0, 1]
    assert rows[0]["health"] == 10
    assert "hp" not in rows[0]
    assert rows[2]["value"] == 7
    assert guard.stats()["exported"] == 3


def test_export_prefix_honours_segment_limit():
    guard = rg.ReplayIntegrityGuard()
    for index in range(3):
        guard.seal_segment(index, [{"type": "damage", "n": index}])
    rows = guard.export_prefix(1)
    assert [row["segment"] for row in rows] == [0, 1]
    assert [row["seq"] for row in rows] == [1, 2]


def test_export_prefix_rejects_bad_limit():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage"}])
    for bad in (-1, "all"):
        with pytest.raises(rg.ReplayGuardError) as exc:
            guard.export_prefix(bad)
        assert exc.value.code == "REPLAY_INVALID_REQUEST"


def test_map_event_v1_renames_legacy_fields():
    guard = rg.ReplayIntegrityGuard()
    mapped = guard.map_event({"type": "damage", "hp": 12, "ts": 5, "src": "boss"},
                             from_version=1)
    assert mapped == {"type": "damage", "health": 12, "tick": 5,
                      "source": "boss", "schema_version": 3}


def test_map_event_v2_and_v3():
    guard = rg.ReplayIntegrityGuard()
    v2 = guard.map_event({"type": "heal", "hp": 4, "amt": 9}, from_version=2)
    assert v2 == {"type": "heal", "health": 4, "value": 9, "schema_version": 3}
    v3 = guard.map_event({"type": "heal", "hp": 4}, from_version=3)
    assert v3 == {"type": "heal", "hp": 4, "schema_version": 3}


def test_map_event_rejects_bad_input():
    guard = rg.ReplayIntegrityGuard()
    for raw, version in (([], 1), ("x", 1), ({"type": "damage"}, 0),
                         ({"type": "damage"}, "1")):
        with pytest.raises(rg.ReplayGuardError) as exc:
            guard.map_event(raw, from_version=version)
        assert exc.value.code == "REPLAY_INVALID_REQUEST"


def test_snapshot_detached_and_restore_roundtrip():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage", "hp": 10}])
    guard.seal_segment(1, [{"type": "damage", "hp": 20}], version=2)
    state = guard.snapshot()
    assert set(state) == {"schema", "segments"}
    pristine = copy.deepcopy(state)
    state["segments"][0]["events"].append({"type": "injected"})
    state["segments"].clear()
    assert guard.verify() is True
    assert guard.export_prefix()[0]["health"] == 10

    clone = rg.ReplayIntegrityGuard()
    clone.restore(pristine)
    assert clone.verify() is True
    assert [row["seq"] for row in clone.export_prefix()] == [1, 2]


def test_restore_rejects_malformed_state():
    guard = rg.ReplayIntegrityGuard()
    guard.seal_segment(0, [{"type": "damage"}])
    bad_states = [
        None,
        {},
        {"schema": 2, "segments": []},
        {"schema": 1, "segments": "x"},
        {"schema": 1, "segments": [{"index": 1, "version": 1,
                                    "events": [{"t": 1}],
                                    "digest": "a", "prev_digest": "b"}]},
    ]
    for bad in bad_states:
        with pytest.raises(rg.ReplayGuardError) as exc:
            guard.restore(bad)
        assert exc.value.code == "REPLAY_STATE_INVALID"
    assert guard.verify() is True
