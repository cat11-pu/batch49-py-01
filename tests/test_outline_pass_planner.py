import math

import pytest

import outline_pass_planner as op


def make(**kwargs):
    return op.OutlinePassPlanner(**kwargs)


def test_set_thresholds_reads_back_as_copy():
    a = make()
    out = a.set_thresholds(depth=0.25, normal=0.5)
    assert out == {"depth": 0.25, "normal": 0.5}
    view = a.thresholds()
    assert view == {"depth": 0.25, "normal": 0.5}
    view["depth"] = 99.0
    assert a.thresholds()["depth"] == 0.25


def test_set_thresholds_rejects_bad_values():
    a = make()
    with pytest.raises(op.OutlineError) as zero_depth:
        a.set_thresholds(depth=0.0, normal=0.85)
    assert zero_depth.value.code == "OUTLINE_BAD_THRESHOLD"
    with pytest.raises(op.OutlineError) as wide_normal:
        a.set_thresholds(depth=0.5, normal=1.5)
    assert wide_normal.value.code == "OUTLINE_BAD_THRESHOLD"
    with pytest.raises(op.OutlineError) as zero_normal:
        a.set_thresholds(depth=0.5, normal=0.0)
    assert zero_normal.value.code == "OUTLINE_BAD_THRESHOLD"
    assert a.thresholds() == {"depth": 0.5, "normal": 0.85}


def test_detect_depth_edge_only():
    a = make()
    assert a.detect_edge(1.0, 0.95) == 1
    assert a.set_thresholds(depth=1.5, normal=0.85) == {"depth": 1.5, "normal": 0.85}
    assert a.detect_edge(1.0, 0.95) == 0


def test_detect_normal_edge_only():
    a = make()
    assert a.detect_edge(0.1, 0.5) == 2


def test_detect_no_edge():
    a = make()
    assert a.detect_edge(0.1, 0.95) == 0


def test_detect_rejects_invalid_sample():
    a = make()
    with pytest.raises(op.OutlineError) as nan:
        a.detect_edge(math.nan, 0.9)
    assert nan.value.code == "OUTLINE_INVALID_SAMPLE"
    with pytest.raises(op.OutlineError) as text:
        a.detect_edge("0.1", 0.9)
    assert text.value.code == "OUTLINE_INVALID_SAMPLE"


def test_width_scales_with_distance():
    a = make()
    assert a.width_for_depth(10.0, 2.0) == 2
    assert a.width_for_depth(5.0, 2.0) == 4
    assert a.width_for_depth(4.0, 2.0) == 5


def test_width_clamped_by_bounds():
    a = make(max_width=6)
    assert a.width_for_depth(100.0, 2.0) == 1
    assert a.width_for_depth(1.0, 2.0) == 6


def test_width_rejects_bad_inputs():
    a = make()
    with pytest.raises(op.OutlineError) as bad_depth:
        a.width_for_depth(0.0, 2.0)
    assert bad_depth.value.code == "OUTLINE_BAD_DEPTH"
    with pytest.raises(op.OutlineError) as bad_width:
        a.width_for_depth(10.0, 0.0)
    assert bad_width.value.code == "OUTLINE_BAD_WIDTH"


def test_pixel_width_zero_without_edges():
    a = make()
    assert a.pixel_width(10.0, 2.0, False, False) == 0
    assert a.pixel_width(10.0, 2.0, True, False) == 2


def test_plan_tiles_counts_interior_samples():
    a = make(grid=(8, 8), max_samples_per_frame=1024)
    plans = a.plan_tiles(2, [(3, 3), (4, 4)])
    assert len(plans) == 2
    assert [p.samples for p in plans] == [24, 24]
    assert [p.trimmed for p in plans] == [False, False]
    assert plans[0].tile == (3, 3)
    assert a.budget() == {"limit": 1024, "used": 48, "remaining": 976}
    assert a.stats()["samples_used"] == 48


def test_plan_tiles_rejects_tile_outside_grid():
    a = make(grid=(8, 8))
    with pytest.raises(op.OutlineError) as outside:
        a.plan_tiles(1, [(8, 0)])
    assert outside.value.code == "OUTLINE_BAD_TILE"
    with pytest.raises(op.OutlineError) as negative:
        a.plan_tiles(1, [(-1, 2)])
    assert negative.value.code == "OUTLINE_BAD_TILE"
    assert a.budget()["used"] == 0


def test_plan_tiles_budget_exceeded_is_atomic():
    a = make(grid=(8, 8), max_samples_per_frame=40)
    with pytest.raises(op.OutlineError) as over:
        a.plan_tiles(2, [(3, 3), (4, 4)])
    assert over.value.code == "OUTLINE_BUDGET_EXCEEDED"
    assert a.budget()["used"] == 0
    assert a.stats()["tiles_planned"] == 0


def test_composite_order_respects_participation():
    a = make()
    objects = [
        {"id": "bg", "transparent": False, "masked": False},
        {"id": "glass", "transparent": True, "masked": False},
        {"id": "cutout", "transparent": False, "masked": True},
        {"id": "hud", "transparent": False, "masked": False},
    ]
    assert a.composite_order(objects) == ("bg", "hud")
    a.set_participation(transparent=True)
    assert a.composite_order(objects) == ("bg", "glass", "hud")
    a.set_participation(transparent=True, masked=True)
    assert a.composite_order(objects) == ("bg", "glass", "cutout", "hud")
    with pytest.raises(op.OutlineError) as bad_id:
        a.composite_order([{"id": "", "transparent": False, "masked": False}])
    assert bad_id.value.code == "OUTLINE_INVALID_OBJECT"


def test_snapshot_restore_roundtrip():
    a = make(grid=(8, 8), max_samples_per_frame=1024)
    a.set_thresholds(depth=0.4, normal=0.7)
    a.set_participation(transparent=True)
    a.plan_tiles(1, [(2, 2)])
    state = a.snapshot()
    b = make(grid=(8, 8), max_samples_per_frame=1024)
    b.restore(state)
    assert b.thresholds() == {"depth": 0.4, "normal": 0.7}
    assert b.budget() == {"limit": 1024, "used": 8, "remaining": 1016}
    assert b.stats() == a.stats()
    objects = [{"id": "glass", "transparent": True, "masked": False}]
    assert b.composite_order(objects) == ("glass",)
    with pytest.raises(op.OutlineError) as bad:
        b.restore({"thresholds": "nope"})
    assert bad.value.code == "OUTLINE_SNAPSHOT_INVALID"
    assert b.thresholds() == {"depth": 0.4, "normal": 0.7}
