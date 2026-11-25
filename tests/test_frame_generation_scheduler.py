import copy
import threading

import pytest

import frame_generation_scheduler as fg


def scheduler(**kwargs):
    return fg.FrameGenerationScheduler(**kwargs)


def test_first_real_frame_plans_nothing():
    s = scheduler()
    assert s.submit_real("r1", 0) == 0
    assert s.plan_slots("r1") == []


def test_wide_gap_gets_two_consecutive_generated_slots():
    s = scheduler()
    s.submit_real("r1", 0)
    assert s.submit_real("r2", 100) == 2
    assert s.plan_slots("r2") == [1, 2]
    assert s.stats()["planned"] == 2


def test_generated_slots_never_exceed_two_even_if_limit_is_higher():
    s = scheduler(max_generated_per_gap=5)
    s.submit_real("r1", 0)
    assert s.submit_real("r2", 200) == 2
    assert s.plan_slots("r2") == [1, 2]


def test_plan_slots_rejects_unknown_frame():
    s = scheduler()
    with pytest.raises(fg.FrameGenError) as exc:
        s.plan_slots("ghost")
    assert exc.value.code == "FRAME_UNKNOWN"


def test_submit_rejects_invalid_and_duplicate_frames():
    s = scheduler()
    with pytest.raises(fg.FrameGenError) as exc:
        s.submit_real("", 0)
    assert exc.value.code == "INVALID_FRAME"
    with pytest.raises(fg.FrameGenError) as exc:
        s.submit_real("r1", -20)
    assert exc.value.code == "INVALID_FRAME"
    s.submit_real("r1", 0)
    with pytest.raises(fg.FrameGenError) as exc:
        s.submit_real("r1", 100)
    assert exc.value.code == "FRAME_DUPLICATE"
    with pytest.raises(fg.FrameGenError) as exc:
        s.set_refresh_rate(0)
    assert exc.value.code == "INVALID_RATE"
    assert s.refresh_rate() == 50


def test_present_generated_slot_returns_frame_id_once():
    s = scheduler()
    s.submit_real("r1", 0)
    s.submit_real("r2", 100)
    assert s.present(1) == "gen:r2:1"
    assert s.stats()["presented"] == 1
    with pytest.raises(fg.FrameGenError) as exc:
        s.present(1)
    assert exc.value.code == "SLOT_ALREADY_PRESENTED"


def test_present_real_slot_returns_original_frame_id():
    s = scheduler()
    s.submit_real("r1", 0)
    assert s.present(0) == "r1"
    assert s.stats()["presented"] == 1


def test_present_empty_slot_is_rejected():
    s = scheduler()
    s.submit_real("r1", 0)
    with pytest.raises(fg.FrameGenError) as exc:
        s.present(4)
    assert exc.value.code == "SLOT_EMPTY"


def test_drop_generated_frame_frees_slot():
    s = scheduler()
    s.submit_real("r1", 0)
    s.submit_real("r2", 100)
    assert s.drop(1) is True
    assert s.stats()["dropped"] == 1
    with pytest.raises(fg.FrameGenError) as exc:
        s.present(1)
    assert exc.value.code == "SLOT_EMPTY"


def test_drop_real_frame_is_rejected():
    s = scheduler()
    s.submit_real("r1", 0)
    with pytest.raises(fg.FrameGenError) as exc:
        s.drop(0)
    assert exc.value.code == "SLOT_NOT_DROPPABLE"


def test_latency_budget_below_frame_period_blocks_generation():
    s = scheduler(latency_budget_ms=10)
    s.submit_real("r1", 0)
    assert s.submit_real("r2", 100) == 0
    assert s.plan_slots("r2") == []
    assert s.latency_budget() == 10


def test_inflight_limit_skips_extra_generated_frames():
    s = scheduler(max_inflight_generated=1)
    s.submit_real("r1", 0)
    assert s.submit_real("r2", 100) == 1
    assert s.submit_real("r3", 200) == 0
    assert s.stats()["skipped"] == 2
    assert s.present(1) == "gen:r2:1"
    assert s.submit_real("r4", 300) == 1
    assert s.stats()["skipped"] == 2


def test_concurrent_present_has_single_winner():
    s = scheduler()
    s.submit_real("r1", 0)
    s.submit_real("r2", 100)
    barrier = threading.Barrier(8)
    winners = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            winners.append(s.present(1))
        except fg.FrameGenError as exc:
            errors.append(exc.code)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert winners == ["gen:r2:1"]
    assert errors == ["SLOT_ALREADY_PRESENTED"] * 7
    assert s.stats()["presented"] == 1


def test_snapshot_is_detached_and_restores():
    s = scheduler()
    s.submit_real("r1", 0)
    s.submit_real("r2", 100)
    s.present(1)
    snap = s.snapshot()
    assert set(snap) == {"version", "frames", "slots", "counters", "config"}
    state = copy.deepcopy(snap)
    clone = scheduler()
    clone.restore(state)
    assert clone.stats() == s.stats()
    assert clone.plan_slots("r2") == s.plan_slots("r2")
    assert clone.present(2) == "gen:r2:2"
    snap["slots"].clear()
    assert s.stats()["presented"] == 1
    assert s.plan_slots("r2") == [1, 2]


def test_restore_rejects_malformed_state():
    s = scheduler()
    s.submit_real("r1", 0)
    bad_states = [
        None,
        {},
        {"version": 1, "frames": [], "slots": {}, "counters": {}},
        {
            "version": 1,
            "frames": {},
            "slots": [],
            "counters": {},
            "config": {},
        },
        {
            "version": 1,
            "frames": {},
            "slots": {},
            "counters": "nope",
            "config": {},
        },
    ]
    for state in bad_states:
        with pytest.raises(fg.FrameGenError) as exc:
            s.restore(state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert s.stats()["real"] == 1
