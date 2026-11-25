import copy
import threading

import pytest

import subtitle_drift_corrector as sd


def corrector(**kwargs):
    return sd.SubtitleDriftCorrector(**kwargs)


def test_add_anchor_reports_new_and_identical_duplicate():
    c = corrector()
    assert c.add_anchor(0, 0, 0) is True
    assert c.add_anchor(1000, 100, 0) is True
    assert c.add_anchor(0, 0, 10) is False
    assert c.anchor_count() == 2
    assert c.add_anchor(2000, 50, 0) is True
    assert c.anchor_count() == 3


def test_map_time_interpolates_and_holds_outside_anchors():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 200, 0)
    c.add_anchor(2000, 200, 0)
    assert c.map_time(500) == 600
    assert c.map_time(1500) == 1700
    assert c.map_time(-500) == -500
    assert c.map_time(3000) == 3200


def test_map_time_clamps_shift_to_limit():
    c = corrector(max_shift_ms=250)
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 900, 0)
    assert c.map_time(500) == 750
    assert c.map_time(1000) == 1250


def test_fit_segments_uses_adjacent_anchor_pairs():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 200, 0)
    c.add_anchor(3000, 600, 0)
    assert c.fit_segments() == [(0, 1000, 0.2), (1000, 3000, 0.2)]


def test_segment_cap_merges_closest_slopes():
    c = corrector(max_segments=2)
    for media_ms, drift_ms in [(0, 0), (1000, 100), (2000, 200), (3000, 500), (4000, 800)]:
        c.add_anchor(media_ms, drift_ms, 0)
    segments = c.fit_segments()
    assert len(segments) == 2
    assert segments[0] == (0, 2000, 0.1)
    assert segments[1] == (2000, 4000, 0.3)


def test_apply_preserves_text_and_duration_in_order():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 100, 0)
    out = c.apply([(0, 500, "a"), (800, 1200, "b"), (1500, 2000, "c")])
    assert out == [(0, 500, "a"), (880, 1280, "b"), (1600, 2100, "c")]


def test_apply_twice_is_idempotent():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 200, 0)
    cues = [(0, 400, "x"), (1000, 1400, "y")]
    first = c.apply(cues)
    second = c.apply(cues)
    assert first == [(0, 400, "x"), (1200, 1600, "y")]
    assert second == first


def test_invalid_cues_rejected_without_side_effects():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 100, 0)
    bad_batches = [
        [(100, 50, "x")],
        [(0, 100, 5)],
        [("0", 100, "x")],
        [(0, 100, "ok"), (200, 100, "bad")],
    ]
    for cues in bad_batches:
        with pytest.raises(sd.SubtitleDriftError) as exc:
            c.apply(cues)
        assert exc.value.code == "CUE_INVALID"
    assert c.stats()["cues"] == 0
    assert c.apply([(0, 100, "ok")]) == [(0, 100, "ok")]


def test_anchor_limit_rejects_extra_anchor():
    c = corrector(max_anchors=2)
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 0, 0)
    with pytest.raises(sd.SubtitleDriftError) as exc:
        c.add_anchor(2000, 0, 0)
    assert exc.value.code == "ANCHOR_LIMIT"
    assert c.anchor_count() == 2


def test_stale_anchors_are_excluded_from_fit():
    c = corrector(anchor_ttl_ms=3500)
    c.add_anchor(0, 1000, 0)
    c.add_anchor(1000, 1000, 4000)
    c.add_anchor(2000, 0, 4000)
    assert c.anchors_for(now_ms=4100) == [(1000, 1000), (2000, 0)]
    assert c.fit_segments(now_ms=4100) == [(1000, 2000, -1.0)]
    assert c.map_time(1500, now_ms=4100) == 2000


def test_max_shift_reports_clamped_correction():
    c = corrector(max_shift_ms=250)
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 800, 0)
    c.apply([(1000, 1500, "a")])
    assert c.max_shift() == 250
    assert c.stats()["max_shift_ms"] == 250


def test_stats_shape_and_counts():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 100, 0)
    c.apply([(500, 900, "a")])
    st = c.stats()
    assert set(st) == {"anchors", "segments", "conflicts", "max_shift_ms", "cues"}
    assert st["anchors"] == 2
    assert st["segments"] == 1
    assert st["conflicts"] == 0
    assert st["max_shift_ms"] == 50
    assert st["cues"] == 1


def test_snapshot_is_detached_and_restores():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 200, 0)
    c.apply([(500, 900, "a")])
    snap = c.snapshot()
    assert set(snap) == {"version", "anchors", "limits", "stats"}
    state = copy.deepcopy(snap)
    clone = corrector()
    clone.restore(state)
    assert clone.map_time(1500) == c.map_time(1500)
    assert clone.stats() == c.stats()
    assert clone.anchor_count() == 2
    snap["anchors"].clear()
    snap["stats"]["cues"] = 99
    assert c.anchor_count() == 2
    assert c.stats()["cues"] == 1


def test_restore_rejects_malformed_state():
    c = corrector()
    c.add_anchor(0, 0, 0)
    c.add_anchor(1000, 100, 0)
    bad_states = [
        None,
        {},
        {"version": 1, "anchors": [], "limits": {}, "stats": {}},
        {
            "version": 1,
            "anchors": [],
            "limits": {"max_anchors": 8},
            "stats": {"anchors": 0, "segments": 0, "conflicts": 0, "max_shift_ms": 0, "cues": 0},
        },
        {"version": 1, "anchors": "nope", "limits": {}, "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(sd.SubtitleDriftError) as exc:
            c.restore(state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert c.map_time(500) == 550


def test_concurrent_anchor_adds_are_not_lost():
    c = corrector()
    errors = []
    barrier = threading.Barrier(16)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            c.add_anchor(index * 100, index, 0)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert c.anchor_count() == 16
    assert c.map_time(700) == 707
