import pytest

import broadphase_sweep_pruner as bp


def pruner(**kwargs):
    return bp.BroadphaseSweepPruner(**kwargs)


def box(pruner_, entity_id, x, y, size=1.0):
    pruner_.upsert(entity_id, x, y, x + size, y + size)


def test_upsert_and_contains():
    p = pruner()
    p.upsert("e1", 0.0, 0.0, 2.0, 3.0)
    assert p.contains("e1") is True
    assert p.contains("nope") is False
    assert p.entity_count() == 1
    found = p.bounds("e1")
    assert (found.min_x, found.min_y, found.max_x, found.max_y) == (0.0, 0.0, 2.0, 3.0)


def test_upsert_rejects_invalid_bounds_and_empty_id():
    p = pruner()
    with pytest.raises(bp.BroadphaseError) as excinfo:
        p.upsert("e1", 5.0, 0.0, 1.0, 2.0)
    assert excinfo.value.code == "INVALID_BOUNDS"
    with pytest.raises(bp.BroadphaseError) as excinfo:
        p.upsert("", 0.0, 0.0, 1.0, 1.0)
    assert excinfo.value.code == "INVALID_ENTITY"
    with pytest.raises(bp.BroadphaseError) as excinfo:
        p.upsert("e2", 0.0, 9.0, 1.0, 2.0)
    assert excinfo.value.code == "INVALID_BOUNDS"


def test_overlapping_pair_reported():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    assert p.query_pairs() == [("a", "b")]


def test_y_separated_pair_not_reported():
    p = pruner()
    p.upsert("a", 0.0, 0.0, 2.0, 1.0)
    p.upsert("b", 1.0, 5.0, 3.0, 6.0)
    assert p.query_pairs() == []


def test_pair_normalized_regardless_of_insert_order():
    p = pruner()
    box(p, "z", 0.0, 0.0)
    box(p, "a", 0.5, 0.5)
    assert p.query_pairs() == [("a", "z")]


def test_insert_order_does_not_change_result():
    def other():
        q = pruner()
        box(q, "o", 0.0, 0.0)
        box(q, "m", 0.2, 0.2)
        box(q, "n", 0.4, 0.4)
        return q

    def forward():
        q = pruner()
        box(q, "m", 0.2, 0.2)
        box(q, "n", 0.4, 0.4)
        box(q, "o", 0.0, 0.0)
        return q

    assert other().query_pairs() == forward().query_pairs()
    assert forward().query_pairs() == [("m", "n"), ("m", "o"), ("n", "o")]


def test_moving_away_removes_pair_and_new_pair_appears():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    assert p.query_pairs() == [("a", "b")]
    p.upsert("a", 10.0, 10.0, 11.0, 11.0)
    assert p.query_pairs() == []
    box(p, "c", 10.5, 10.5)
    assert p.query_pairs() == [("a", "c")]


def test_budget_caps_pairs_per_frame():
    p = pruner()
    for entity_id in ("a", "b", "c"):
        box(p, entity_id, 0.0, 0.0)
    p.set_budget(1)
    assert len(p.query_pairs()) == 1
    stats = p.stats()
    assert stats["reported"] == 1
    assert stats["pending"] == 2


def test_truncated_pairs_appear_over_later_frames():
    p = pruner()
    for entity_id in ("a", "b", "c", "d"):
        box(p, entity_id, 0.0, 0.0)
    p.set_budget(2)
    seen = set()
    for _ in range(6):
        frame = p.query_pairs()
        assert len(frame) <= 2
        seen.update(frame)
    expected = {("a", "b"), ("a", "c"), ("a", "d"),
                ("b", "c"), ("b", "d"), ("c", "d")}
    assert seen == expected


def test_set_budget_controls_query():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    p.set_budget(0)
    assert p.query_pairs() == []
    p.set_budget(5)
    assert p.query_pairs() == [("a", "b")]


def test_remove_unknown_returns_false():
    p = pruner()
    box(p, "solo", 0.0, 0.0)
    assert p.remove("solo") is True
    assert p.remove("solo") is False
    assert p.entity_count() == 0


def test_remove_isolated_entity_updates_counts():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    box(p, "solo", 20.0, 20.0)
    assert p.query_pairs() == [("a", "b")]
    assert p.remove("solo") is True
    assert p.entity_count() == 2
    assert p.contains("solo") is False


def test_bounds_lookup_rejects_unknown_entity():
    p = pruner()
    with pytest.raises(bp.BroadphaseError) as excinfo:
        p.bounds("ghost")
    assert excinfo.value.code == "UNKNOWN_ENTITY"


def test_stats_counts_entities_candidates_and_cache():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    box(p, "c", 20.0, 20.0)
    assert p.query_pairs() == [("a", "b")]
    stats = p.stats()
    assert stats["entities"] == 3
    assert stats["candidates"] == 1
    assert stats["reported"] == 1
    assert stats["pending"] == 0
    assert stats["cache"] == 1
    assert stats["budget"] == 64


def test_snapshot_restore_roundtrip_and_invalid_state():
    p = pruner()
    box(p, "a", 0.0, 0.0)
    box(p, "b", 0.5, 0.5)
    assert p.query_pairs() == [("a", "b")]
    saved = p.snapshot()
    box(p, "c", 20.0, 20.0)
    assert p.entity_count() == 3
    p.restore(saved)
    assert p.entity_count() == 2
    assert p.contains("c") is False
    restored = p.bounds("a")
    assert (restored.min_x, restored.max_x) == (0.0, 1.0)
    p.upsert("a", 0.1, 0.1, 1.1, 1.1)
    assert p.query_pairs() == [("a", "b")]
    with pytest.raises(bp.BroadphaseError) as excinfo:
        p.restore({"bad": "state"})
    assert excinfo.value.code == "INVALID_SNAPSHOT"
    assert p.entity_count() == 2
