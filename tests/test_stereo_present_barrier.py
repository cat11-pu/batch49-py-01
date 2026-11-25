import copy
import threading

import pytest

import stereo_present_barrier as sb


def barrier(**kwargs):
    return sb.StereoPresentBarrier(**kwargs)


def fill(p, pair_id, at_ms=0):
    p.submit_view(pair_id, "left", b"L", at_ms)
    p.submit_view(pair_id, "right", b"R", at_ms)


def test_begin_pair_rejects_duplicate_pair():
    p = barrier()
    p.begin_pair("p1", 0)
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.begin_pair("p1", 1)
    assert exc.value.code == "PAIR_EXISTS"
    assert p.inflight() == ["p1"]


def test_begin_pair_rejects_invalid_arguments():
    p = barrier()
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.begin_pair("", 0)
    assert exc.value.code == "PAIR_INVALID"
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.begin_pair("p2", -1)
    assert exc.value.code == "PAIR_INVALID"
    assert p.inflight() == []


def test_pair_limit_frees_slot_after_present():
    p = barrier(max_inflight_pairs=2)
    p.begin_pair("p1", 0)
    p.begin_pair("p2", 0)
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.begin_pair("p3", 0)
    assert exc.value.code == "PAIR_LIMIT"
    fill(p, "p1")
    assert p.present_pair("p1") == (b"L", b"R")
    p.begin_pair("p3", 0)
    assert p.inflight() == ["p2", "p3"]


def test_submit_view_rejects_unknown_pair_and_bad_view():
    p = barrier()
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.submit_view("ghost", "left", b"x", 0)
    assert exc.value.code == "PAIR_UNKNOWN"
    p.begin_pair("p1", 0)
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.submit_view("p1", "top", b"x", 0)
    assert exc.value.code == "VIEW_INVALID"
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.submit_view("p1", "left", b"", 0)
    assert exc.value.code == "VIEW_INVALID"


def test_submit_view_reports_pair_completion():
    p = barrier()
    p.begin_pair("p1", 0)
    assert p.submit_view("p1", "left", b"L", 0) is False
    assert p.submit_view("p1", "right", b"R", 0) is True
    assert p.inflight() == ["p1"]
    assert p.stats()["pairs"] == 1


def test_present_pair_requires_both_views():
    p = barrier()
    p.begin_pair("p1", 0)
    p.submit_view("p1", "left", b"L", 0)
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.present_pair("p1")
    assert exc.value.code == "PAIR_INCOMPLETE"
    assert p.stats()["presented"] == 0


def test_present_pair_returns_views_once():
    p = barrier()
    p.begin_pair("p1", 0)
    fill(p, "p1")
    assert p.present_pair("p1") == (b"L", b"R")
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.present_pair("p1")
    assert exc.value.code == "PAIR_TAKEN"
    assert p.inflight() == []
    assert p.stats()["presented"] == 1


def test_wait_barrier_is_ready_when_both_views_present():
    p = barrier()
    p.begin_pair("p1", 0)
    fill(p, "p1")
    assert p.wait_barrier("p1", 5) == "ready"


def test_wait_barrier_reports_waiting_and_timeout():
    p = barrier()
    p.begin_pair("p1", 0)
    p.submit_view("p1", "left", b"L", 0)
    assert p.wait_barrier("p1", 5) == "waiting"
    assert p.wait_barrier("p1", 13) == "timedout"


def test_timeout_drops_only_stale_pairs():
    p = barrier()
    p.begin_pair("p1", 0)
    p.begin_pair("p2", 10)
    p.submit_view("p1", "left", b"L", 0)
    p.submit_view("p2", "left", b"L", 10)
    assert p.timeout(20) == ["p1"]
    assert p.inflight() == ["p2"]
    st = p.stats()
    assert st["dropped_pairs"] == 1
    assert st["dropped_views"] == 1
    assert st["timeouts"] == 1
    assert st["presented"] == 0
    assert p.dropped_ids() == ["p1"]


def test_drop_pair_counts_submitted_views():
    p = barrier()
    p.begin_pair("p1", 0)
    assert p.drop_pair("p1") == 0
    p.begin_pair("p2", 0)
    p.submit_view("p2", "left", b"L", 0)
    assert p.drop_pair("p2") == 1
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.drop_pair("p2")
    assert exc.value.code == "PAIR_TAKEN"
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.present_pair("p2")
    assert exc.value.code == "PAIR_TAKEN"
    st = p.stats()
    assert st["dropped_pairs"] == 2
    assert st["dropped_views"] == 1


def test_concurrent_present_and_timeout_has_single_outcome():
    p = barrier()
    p.begin_pair("p1", 0)
    fill(p, "p1")
    start = threading.Barrier(2)
    results = {}

    def presenter():
        try:
            start.wait(timeout=5)
            results["present"] = "ok"
            results["payload"] = p.present_pair("p1")
        except sb.StereoBarrierError as exc:
            results["present"] = exc.code

    def timer():
        try:
            start.wait(timeout=5)
            results["timeout"] = p.timeout(100)
        except sb.StereoBarrierError as exc:  # pragma: no cover - unexpected
            results["timeout"] = exc.code

    threads = [threading.Thread(target=presenter), threading.Thread(target=timer)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    st = p.stats()
    assert st["presented"] + st["dropped_pairs"] == 1
    if results["present"] == "ok":
        assert results["payload"] == (b"L", b"R")
        assert results["timeout"] == []
        assert st["presented"] == 1
    else:
        assert results["present"] == "PAIR_TAKEN"
        assert results["timeout"] == ["p1"]
        assert st["dropped_pairs"] == 1


def test_timeout_rejects_invalid_clock():
    p = barrier()
    p.begin_pair("p1", 0)
    with pytest.raises(sb.StereoBarrierError) as exc:
        p.timeout(-1)
    assert exc.value.code == "TIME_INVALID"
    assert p.inflight() == ["p1"]


def test_snapshot_is_detached_and_restores():
    p = barrier()
    p.begin_pair("p1", 0)
    fill(p, "p1")
    p.present_pair("p1")
    p.begin_pair("p2", 0)
    p.submit_view("p2", "left", b"L", 0)
    snap = p.snapshot()
    assert set(snap) == {"version", "pairs", "dropped_ids", "stats"}
    state = copy.deepcopy(snap)
    clone = barrier()
    clone.restore(state)
    assert clone.inflight() == p.inflight() == ["p2"]
    assert clone.stats() == p.stats()
    p.submit_view("p2", "right", b"R", 0)
    clone.submit_view("p2", "right", b"R", 0)
    assert clone.present_pair("p2") == (b"L", b"R")
    snap["pairs"].clear()
    assert p.inflight() == ["p2"]


def test_restore_rejects_malformed_state():
    p = barrier()
    p.begin_pair("p1", 0)
    bad_states = [
        None,
        {},
        {"version": 1, "pairs": {}, "dropped_ids": []},
        {"version": 1, "pairs": [], "dropped_ids": [], "stats": {}},
        {"version": 1, "pairs": {}, "dropped_ids": "nope", "stats": {}},
    ]
    for state in bad_states:
        with pytest.raises(sb.StereoBarrierError) as exc:
            p.restore(state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert p.inflight() == ["p1"]
    assert p.stats()["pairs"] == 1
