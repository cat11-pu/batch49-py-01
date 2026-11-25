import pytest

import multiview_lod_arbiter as lod


def make(**kwargs):
    return lod.MultiViewLodArbiter(**kwargs)


def test_view_registration_limits_and_duplicates():
    a = make(max_views=2)
    first = a.register_view("main", 5)
    a.register_view("shadow", 1)
    assert (first.view_id, first.priority) == ("main", 5)
    with pytest.raises(lod.LodError) as limit:
        a.register_view("probe")
    assert limit.value.code == "LOD_VIEW_LIMIT"
    with pytest.raises(lod.LodError) as dupe:
        a.register_view("main")
    assert dupe.value.code == "LOD_DUPLICATE_VIEW"
    assert a.stats()["views"] == 2


def test_unknown_view_and_priority_rejected():
    a = make()
    with pytest.raises(lod.LodError) as req:
        a.request("ghost", "rock", 1, 0)
    assert req.value.code == "LOD_UNKNOWN_VIEW"
    with pytest.raises(lod.LodError) as prio:
        a.set_view_priority("ghost", 9)
    assert prio.value.code == "LOD_UNKNOWN_VIEW"
    assert a.stats()["requests"] == 0


def test_request_rejects_out_of_range_lod_and_frame():
    a = make(max_lod=3)
    a.register_view("main")
    for bad in (-1, 4):
        with pytest.raises(lod.LodError) as err:
            a.request("main", "rock", bad, 0)
        assert err.value.code == "LOD_LOD_RANGE"
    with pytest.raises(lod.LodError) as frame:
        a.request("main", "rock", 1, -1)
    assert frame.value.code == "LOD_BAD_FRAME"
    assert a.stats()["requests"] == 0


def test_resident_defaults_to_coarsest_level():
    a = make(max_lod=3)
    assert a.resident_lod("untouched") == 3
    assert a.stats()["objects"] == 0


def test_arbitrate_merges_views_to_highest_precision():
    a = make(max_lod=3)
    a.register_view("main", 5)
    a.register_view("reflection", 2)
    a.request("main", "rock", 2, 0)
    a.request("reflection", "rock", 3, 0)
    d = a.arbitrate("rock", 0)
    assert (d.object_id, d.demanded, d.resident) == ("rock", 2, 2)
    assert d.switched == "upgrade"
    assert d.deferred is False
    assert a.resident_lod("rock") == 2
    assert a.stats()["upgrades"] == 1


def test_arbitrate_without_new_demand_keeps_resident():
    a = make(max_lod=2)
    a.register_view("main")
    a.request("main", "rock", 1, 3)
    a.arbitrate("rock", 3)
    d = a.arbitrate("rock", 4)
    assert d.switched == "none"
    assert d.demanded == 1
    assert d.resident == 1
    assert a.resident_lod("rock") == 1


def test_downgrade_is_immediate_and_not_budgeted():
    a = make(max_lod=1, frame_upgrade_budget=2)
    a.register_view("main", 5)
    a.request("main", "rock", 0, 0)
    a.arbitrate("rock", 0)
    assert a.resident_lod("rock") == 0
    assert a.upgrade_budget(0) == 1
    a.request("main", "rock", 1, 1)
    d = a.arbitrate("rock", 1)
    assert d.switched == "downgrade"
    assert a.resident_lod("rock") == 1
    assert a.upgrade_budget(1) == 2
    assert a.stats()["downgrades"] == 1


def test_upgrade_budget_shared_in_priority_order():
    a = make(max_lod=2, frame_upgrade_budget=1)
    a.register_view("low", 1)
    a.register_view("high", 9)
    a.request("low", "stone", 1, 5)
    a.request("high", "tree", 1, 5)
    result = a.arbitrate_frame(5)
    assert result["tree"] == 1
    assert result["stone"] == 2
    assert a.upgrade_budget(5) == 0
    assert a.stats()["deferred"] == 1


def test_budget_resets_per_frame():
    a = make(max_lod=2, frame_upgrade_budget=1)
    a.register_view("main")
    a.request("main", "a", 1, 0)
    a.arbitrate("a", 0)
    assert a.upgrade_budget(0) == 0
    assert a.upgrade_budget(1) == 1
    a.request("main", "b", 1, 1)
    assert a.arbitrate("b", 1).switched == "upgrade"
    assert a.upgrade_budget(1) == 0


def test_cooldown_blocks_upgrade_after_downgrade():
    a = make(max_lod=3, cooldown_frames=2, frame_upgrade_budget=4)
    a.register_view("main", 5)
    a.request("main", "rock", 0, 0)
    a.arbitrate("rock", 0)
    assert a.resident_lod("rock") == 0
    a.request("main", "rock", 3, 2)
    assert a.arbitrate("rock", 2).switched == "downgrade"
    a.request("main", "rock", 2, 3)
    blocked = a.arbitrate("rock", 3)
    assert blocked.switched == "none"
    assert blocked.deferred is True
    assert a.resident_lod("rock") == 3
    a.request("main", "rock", 2, 4)
    allowed = a.arbitrate("rock", 4)
    assert allowed.switched == "upgrade"
    assert a.resident_lod("rock") == 2


def test_hysteresis_requires_gap_before_downgrade():
    a = make(max_lod=2, hysteresis=2, frame_upgrade_budget=4)
    a.register_view("main")
    a.request("main", "rock", 0, 0)
    a.arbitrate("rock", 0)
    assert a.resident_lod("rock") == 0
    a.request("main", "rock", 1, 1)
    within = a.arbitrate("rock", 1)
    assert within.switched == "none"
    assert a.resident_lod("rock") == 0
    a.request("main", "rock", 2, 2)
    over = a.arbitrate("rock", 2)
    assert over.switched == "downgrade"
    assert a.resident_lod("rock") == 2


def test_unregister_view_drops_pending_demands():
    a = make(max_lod=3)
    a.register_view("main", 5)
    a.register_view("shadow", 1)
    a.request("main", "rock", 1, 0)
    a.request("shadow", "rock", 1, 0)
    assert a.unregister_view("shadow") == 1
    with pytest.raises(lod.LodError) as gone:
        a.request("shadow", "rock", 1, 0)
    assert gone.value.code == "LOD_UNKNOWN_VIEW"
    assert a.stats()["views"] == 1


def test_snapshot_restore_roundtrip():
    a = make(max_lod=1)
    a.register_view("main", 5)
    a.request("main", "rock", 0, 0)
    a.arbitrate("rock", 0)
    state = a.snapshot()
    b = make(max_lod=1)
    b.restore(state)
    assert b.resident_lod("rock") == 0
    assert b.stats() == a.stats()
    assert b.stats()["views"] == 1


def test_restore_rejects_malformed_state():
    a = make()
    with pytest.raises(lod.LodError) as bad:
        a.restore({"views": "nope"})
    assert bad.value.code == "LOD_SNAPSHOT_INVALID"
    assert a.stats()["views"] == 0
    assert a.stats()["requests"] == 0


def test_stats_accumulate():
    a = make(max_lod=3, frame_upgrade_budget=4)
    a.register_view("main", 5)
    a.request("main", "a", 2, 0)
    a.arbitrate("a", 0)
    a.request("main", "a", 3, 1)
    a.arbitrate("a", 1)
    assert a.stats() == {"views": 1, "objects": 1, "upgrades": 1,
                         "downgrades": 1, "deferred": 0, "requests": 2}
