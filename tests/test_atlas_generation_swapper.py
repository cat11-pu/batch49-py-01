import copy

import pytest

import atlas_generation_swapper as ag


def swapper(**kwargs):
    return ag.AtlasGenerationSwapper(**kwargs)


def build(s, generation, page_id, slot_id="hero",
          rect=(0.0, 0.0, 0.5, 0.5)):
    s.begin_generation(generation)
    s.stage_page(generation, page_id)
    s.stage_uv(generation, slot_id, page_id, *rect)


def test_begin_generation_rejects_duplicate_and_empty_ids():
    s = swapper()
    assert s.begin_generation("g1") == "g1"
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.begin_generation("g1")
    assert exc.value.code == "ATLAS_GEN_DUPLICATE"
    for bad in ("", "   "):
        with pytest.raises(ag.AtlasGenerationError) as exc:
            s.begin_generation(bad)
        assert exc.value.code == "ATLAS_GEN_INVALID"
    assert s.generations() == ("g1",)


def test_stage_page_enforces_page_cap_and_duplicate_ids():
    s = swapper(max_pages_per_generation=2)
    s.begin_generation("g1")
    s.stage_page("g1", "p1")
    s.stage_page("g1", "p2")
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.stage_page("g1", "p3")
    assert exc.value.code == "ATLAS_PAGE_LIMIT"
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.stage_page("g1", "p1")
    assert exc.value.code == "ATLAS_PAGE_DUPLICATE"


def test_stage_uv_validates_generation_page_and_coordinates():
    s = swapper()
    s.begin_generation("g1")
    s.stage_page("g1", "p1")
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.stage_uv("ghost", "hero", "p1", 0.0, 0.0, 1.0, 1.0)
    assert exc.value.code == "ATLAS_GEN_UNKNOWN"
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.stage_uv("g1", "hero", "p9", 0.0, 0.0, 1.0, 1.0)
    assert exc.value.code == "ATLAS_PAGE_UNKNOWN"
    for rect in [(-0.1, 0.0, 0.5, 0.5), (0.5, 0.0, 0.5, 0.5),
                 (0.0, 0.0, 0.0, 0.5), (0.0, 0.0, 0.5, 1.2)]:
        with pytest.raises(ag.AtlasGenerationError) as exc:
            s.stage_uv("g1", "hero", "p1", *rect)
        assert exc.value.code == "ATLAS_UV_INVALID"


def test_commit_returns_ordinals_and_switches_active_generation():
    s = swapper()
    build(s, "g1", "p1")
    assert s.commit_generation("g1") == 1
    assert s.active_generation() == "g1"
    build(s, "g2", "p2")
    assert s.commit_generation("g2") == 2
    assert s.active_generation() == "g2"
    assert s.generations() == ("g1", "g2")


def test_commit_rejects_incomplete_generation_and_keeps_old_mapping():
    s = swapper()
    build(s, "g1", "p1", rect=(0.0, 0.0, 0.25, 0.25))
    s.commit_generation("g1")
    before = s.lookup("hero")
    s.begin_generation("g2")
    s.stage_page("g2", "p2")
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.commit_generation("g2")
    assert exc.value.code == "ATLAS_GEN_INCOMPLETE"
    assert s.active_generation() == "g1"
    assert s.generations() == ("g1",)
    after = s.lookup("hero")
    assert after == before
    assert (after.generation, after.page_id) == ("g1", "p1")


def test_lookup_returns_the_active_mapping():
    s = swapper()
    build(s, "g1", "p1", rect=(0.0, 0.0, 0.5, 0.5))
    s.commit_generation("g1")
    mapping = s.lookup("hero")
    assert mapping == ag.UVMapping("g1", "hero", "p1", 0.0, 0.0, 0.5, 0.5)


def test_lookup_rejects_unknown_slot_and_generation():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.lookup("ghost")
    assert exc.value.code == "ATLAS_SLOT_UNKNOWN"
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.lookup("hero", generation="g9")
    assert exc.value.code == "ATLAS_GEN_UNKNOWN"


def test_pin_and_unpin_track_outstanding_handles():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    first = s.pin()
    second = s.pin()
    assert first != second
    assert s.unpin(first) == 1
    assert s.unpin(second) == 0
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.unpin(first)
    assert exc.value.code == "ATLAS_PIN_UNKNOWN"
    assert s.stats()["outstanding_pins"] == 0


def test_pin_limit_is_enforced():
    s = swapper(max_pins=2)
    build(s, "g1", "p1")
    s.commit_generation("g1")
    handles = [s.pin(), s.pin()]
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.pin()
    assert exc.value.code == "ATLAS_PIN_LIMIT"
    for handle in handles:
        s.unpin(handle)
    assert s.pin() > 0


def test_collect_releases_pages_of_retired_generations():
    s = swapper()
    build(s, "g1", "p1")
    s.stage_page("g1", "p2")
    s.commit_generation("g1")
    build(s, "g2", "p3")
    s.commit_generation("g2")
    assert s.collect() == ("p1", "p2")
    assert s.collect() == ()
    assert s.generations() == ("g2",)
    assert s.stats()["collected_pages"] == 2


def test_stage_page_rejects_committed_generation():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.stage_page("g1", "p2")
    assert exc.value.code == "ATLAS_GEN_COMMITTED"


def test_rollback_returns_to_previous_generation():
    s = swapper()
    build(s, "g1", "p1", rect=(0.0, 0.0, 0.25, 0.25))
    s.commit_generation("g1")
    build(s, "g2", "p2", rect=(0.25, 0.0, 0.5, 0.25))
    s.commit_generation("g2")
    assert s.rollback() == "g1"
    assert s.active_generation() == "g1"
    mapping = s.lookup("hero")
    assert (mapping.generation, mapping.page_id) == ("g1", "p1")
    assert (mapping.u0, mapping.u1) == (0.0, 0.25)
    assert s.stats()["rollbacks"] == 1
    with pytest.raises(ag.AtlasGenerationError) as exc:
        s.rollback()
    assert exc.value.code == "ATLAS_NO_ROLLBACK"


def test_snapshot_is_detached_and_restore_roundtrips():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    build(s, "g2", "p2", rect=(0.5, 0.5, 1.0, 1.0))
    s.commit_generation("g2")
    state = s.snapshot()
    assert set(state) == {"version", "generations", "active", "history",
                          "pins", "next_pin", "counters"}
    pristine = copy.deepcopy(state)
    state["generations"].clear()
    state["active"] = "broken"
    assert s.active_generation() == "g2"
    clone = swapper()
    clone.restore(pristine)
    assert clone.active_generation() == "g2"
    assert clone.lookup("hero") == s.lookup("hero")
    assert clone.stats() == s.stats()


def test_restore_rejects_malformed_state():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    bad_states = [
        None,
        {},
        {"version": 7, "generations": [], "active": None, "history": [],
         "pins": {}, "next_pin": 1, "counters": {}},
        {"version": 1, "generations": {}, "active": None, "history": [],
         "pins": {}, "next_pin": 1, "counters": {}},
        {"version": 1, "generations": [], "active": None, "history": [],
         "pins": [], "next_pin": 1, "counters": {}},
        {"version": 1, "generations": [], "active": None, "history": [],
         "pins": {}, "next_pin": 1, "counters": {"commits": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(ag.AtlasGenerationError) as exc:
            s.restore(bad)
        assert exc.value.code == "ATLAS_SNAPSHOT_INVALID"
    assert s.active_generation() == "g1"
    assert s.lookup("hero").page_id == "p1"


def test_stats_track_each_operation():
    s = swapper()
    build(s, "g1", "p1")
    s.commit_generation("g1")
    build(s, "g2", "p2", rect=(0.5, 0.5, 1.0, 1.0))
    s.stage_page("g2", "p3")
    s.commit_generation("g2")
    handle = s.pin()
    s.unpin(handle)
    assert s.stats() == {
        "begun": 2,
        "pages_staged": 3,
        "uv_staged": 2,
        "commits": 2,
        "pins": 1,
        "unpins": 1,
        "collected_pages": 0,
        "rollbacks": 0,
        "outstanding_pins": 0,
        "generations": 2,
    }
