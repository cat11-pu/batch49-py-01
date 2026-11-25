import copy

import pytest

import atlas_gutter_builder as ag


def builder(**kwargs):
    return ag.AtlasGutterBuilder(128, 128, **kwargs)


def spaced_page():
    page = builder()
    page.add_slot("hero", 40, 40, 30, 30)
    page.add_slot("icon", 90, 40, 20, 20)
    return page


def by_side(plan):
    return {rect.side: (rect.x, rect.y, rect.width, rect.height)
            for rect in plan.rects}


def test_add_slot_validates_page_bounds_and_size():
    page = builder()
    bad_calls = [
        dict(slot_id="", x=10, y=10, width=10, height=10),
        dict(slot_id="a", x=-1, y=0, width=10, height=10),
        dict(slot_id="a", x=0, y=0, width=0, height=10),
        dict(slot_id="a", x=100, y=10, width=40, height=10),
        dict(slot_id="a", x=10, y=120, width=10, height=20),
    ]
    for kwargs in bad_calls:
        with pytest.raises(ag.AtlasGutterError) as exc:
            page.add_slot(**kwargs)
        assert exc.value.code == "ATLAS_SLOT_INVALID"
    assert page.slots() == ()


def test_add_slot_rejects_overlap_and_duplicate_ids():
    page = builder()
    page.add_slot("bg", 0, 0, 50, 50)
    with pytest.raises(ag.AtlasGutterError) as exc:
        page.add_slot("fg", 10, 10, 20, 20)
    assert exc.value.code == "ATLAS_SLOT_OVERLAP"
    with pytest.raises(ag.AtlasGutterError) as exc:
        page.add_slot("bg", 0, 0, 50, 50)
    assert exc.value.code == "ATLAS_SLOT_DUPLICATE"
    assert page.slots() == ("bg",)


def test_slots_are_listed_in_sorted_order():
    page = builder()
    page.add_slot("icon", 90, 40, 20, 20)
    page.add_slot("hero", 40, 40, 30, 30)
    assert page.slots() == ("hero", "icon")
    assert page.slot("hero") == ag.Slot("hero", 40, 40, 30, 30)


def test_plan_gutters_builds_strips_and_corners_for_spaced_slots():
    page = spaced_page()
    plans = page.plan_gutters(3)
    assert [plan.slot_id for plan in plans] == ["hero", "icon"]
    hero = plans[0]
    assert hero.declared == 3
    assert hero.actual == 3
    assert len(hero.rects) == 8
    sides = by_side(hero)
    assert set(sides) == {"left", "right", "top", "bottom",
                          "tl", "tr", "bl", "br"}
    assert sides["left"] == (37, 40, 3, 30)
    assert sides["right"] == (70, 40, 3, 30)
    assert sides["top"] == (40, 37, 30, 3)
    assert sides["bottom"] == (40, 70, 30, 3)


def test_plan_gutters_rejects_bad_declared_width():
    page = spaced_page()
    for declared in (0, -1, 9, 2.5):
        with pytest.raises(ag.AtlasGutterError) as exc:
            page.plan_gutters(declared)
        assert exc.value.code == "ATLAS_GUTTER_INVALID"


def test_plan_gutters_rejects_unknown_slot_request():
    page = spaced_page()
    with pytest.raises(ag.AtlasGutterError) as exc:
        page.plan_gutters(2, slot_ids=["ghost"])
    assert exc.value.code == "ATLAS_SLOT_UNKNOWN"
    only_hero = page.plan_gutters(2, slot_ids=["hero"])
    assert [plan.slot_id for plan in only_hero] == ["hero"]


def test_corner_rects_are_square_and_aligned_with_slot_edges():
    page = spaced_page()
    hero = page.plan_gutters(3)[0]
    sides = by_side(hero)
    for corner in ("tl", "tr", "bl", "br"):
        x, y, width, height = sides[corner]
        assert width == height == 3
    assert sides["tl"] == (37, 37, 3, 3)
    assert sides["tr"] == (70, 37, 3, 3)
    assert sides["bl"] == (37, 70, 3, 3)
    assert sides["br"] == (70, 70, 3, 3)


def test_build_level_halves_gutter_per_level():
    page = spaced_page()
    page.plan_gutters(5)
    assert [page.build_level(level).gutter for level in range(4)] == [5, 2, 1, 1]


def test_build_level_rejects_level_outside_mip_chain():
    page = spaced_page()
    page.plan_gutters(2)
    for level in (-1, 4):
        with pytest.raises(ag.AtlasGutterError) as exc:
            page.build_level(level)
        assert exc.value.code == "ATLAS_LEVEL_INVALID"


def test_build_level_reuses_cached_level_until_invalidated():
    page = spaced_page()
    page.plan_gutters(2)
    first = page.build_level(0)
    second = page.build_level(0)
    assert first == second
    assert page.stats()["builds"] == 1
    assert page.stats()["levels"] == 1
    assert page.invalidate_region("hero", 2, 2, 4, 4) == 1
    rebuilt = page.build_level(0)
    assert rebuilt.bytes_used == first.bytes_used
    assert page.stats()["builds"] == 2
    assert page.stats()["levels"] == 1


def test_invalidate_region_merges_touching_rects():
    page = spaced_page()
    assert page.invalidate_region("hero", 1, 1, 4, 4) == 1
    assert page.invalidate_region("hero", 3, 3, 5, 5) == 1
    rects = page.dirty_rects()
    assert len(rects) == 1
    assert page.invalidate_region("hero", 20, 20, 5, 5) == 2
    assert page.dirty_rects() == rects + ((20, 20, 5, 5),)


def test_invalidate_region_rejects_unknown_slot_and_bad_rect():
    page = spaced_page()
    with pytest.raises(ag.AtlasGutterError) as exc:
        page.invalidate_region("ghost", 0, 0, 2, 2)
    assert exc.value.code == "ATLAS_SLOT_UNKNOWN"
    for rect in [(-1, 0, 4, 4), (0, 0, 0, 4), (25, 25, 10, 10)]:
        with pytest.raises(ag.AtlasGutterError) as exc:
            page.invalidate_region("hero", *rect)
        assert exc.value.code == "ATLAS_RECT_INVALID"


def test_budget_caps_total_gutter_bytes():
    page = ag.AtlasGutterBuilder(128, 128, budget_bytes=200)
    page.add_slot("tile", 40, 40, 10, 10)
    page.plan_gutters(2)
    with pytest.raises(ag.AtlasGutterError) as exc:
        page.build_level(0)
    assert exc.value.code == "ATLAS_BUDGET_EXCEEDED"
    report = page.budget()
    assert report["limit_bytes"] == 200
    assert report["used_bytes"] == 0
    assert report["levels"] == 0


def test_snapshot_is_detached_and_restore_roundtrips():
    page = spaced_page()
    page.plan_gutters(2)
    page.build_level(0)
    page.invalidate_region("icon", 0, 0, 4, 4)
    state = page.snapshot()
    assert set(state) == {"version", "page", "slots", "plans", "levels",
                          "dirty", "counters"}
    pristine = copy.deepcopy(state)
    state["slots"].clear()
    state["counters"]["builds"] = 77
    assert page.slots() == ("hero", "icon")
    assert page.stats()["builds"] == 1
    clone = builder()
    clone.restore(pristine)
    assert clone.slots() == ("hero", "icon")
    assert clone.stats() == page.stats()
    assert clone.dirty_rects() == page.dirty_rects()


def test_restore_rejects_malformed_state():
    page = spaced_page()
    page.plan_gutters(2)
    bad_states = [
        None,
        {},
        {"version": 9, "page": [128, 128], "slots": [], "plans": [],
         "levels": [], "dirty": [], "counters": {}},
        {"version": 1, "page": [128, 128], "slots": "nope", "plans": [],
         "levels": [], "dirty": [], "counters": {}},
        {"version": 1, "page": [128, 128], "slots": [], "plans": [],
         "levels": {}, "dirty": [], "counters": {}},
        {"version": 1, "page": [128, 128], "slots": [], "plans": [],
         "levels": [], "dirty": [], "counters": {"builds": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(ag.AtlasGutterError) as exc:
            page.restore(bad)
        assert exc.value.code == "ATLAS_SNAPSHOT_INVALID"
    assert page.slots() == ("hero", "icon")
    assert page.stats()["planned"] == 2
