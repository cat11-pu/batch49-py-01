import threading

import pytest

import texture_prefetch_queue as tq


def world(**kwargs):
    queue = tq.TexturePrefetchQueue(**kwargs)
    queue.set_world(12)
    queue.set_pose(0.0, 0.0, 10.0, 5.0)
    return queue


def test_set_world_rejects_bad_sector_count():
    queue = tq.TexturePrefetchQueue()
    for count in (0, 4097, -3):
        with pytest.raises(tq.TextureError) as exc:
            queue.set_world(count)
        assert exc.value.code == "TEXTURE_INVALID_WORLD"
    with pytest.raises(tq.TextureError) as exc:
        queue.set_pose(0.0, 0.0, 0.0, 1.0)
    assert exc.value.code == "TEXTURE_NO_WORLD"


def test_set_pose_rejects_invalid_values():
    queue = world()
    bad_calls = [(0.0, 0.0, 0.0, -1.0),
                 (0.0, 0.0, float("nan"), 1.0),
                 (float("inf"), 0.0, 0.0, 1.0)]
    for args in bad_calls:
        with pytest.raises(tq.TextureError) as exc:
            queue.set_pose(*args)
        assert exc.value.code == "TEXTURE_INVALID_POSE"


def test_predict_sectors_ahead_of_heading():
    queue = world(lookahead=2)
    assert queue.predict_sectors() == [0, 1, 2]


def test_predict_sectors_wraps_around_ring():
    queue = tq.TexturePrefetchQueue()
    queue.set_world(12)
    queue.set_pose(0.0, 0.0, 350.0, 3.0)
    assert queue.predict_sectors() == [11, 0, 1]


def test_predict_without_pose_is_rejected():
    queue = tq.TexturePrefetchQueue()
    queue.set_world(12)
    with pytest.raises(tq.TextureError) as exc:
        queue.predict_sectors()
    assert exc.value.code == "TEXTURE_NO_POSE"


def test_enqueue_dedupes_same_sector():
    queue = world()
    assert queue.enqueue(3) is True
    assert queue.enqueue(3) is False
    assert queue.budget()["queued"] == 1
    with pytest.raises(tq.TextureError) as exc:
        queue.enqueue(12)
    assert exc.value.code == "TEXTURE_INVALID_SECTOR"


def test_next_decode_follows_angular_distance():
    queue = world()
    for sector in (2, 0, 1):
        queue.enqueue(sector)
    assert [queue.next_decode(), queue.next_decode(), queue.next_decode()] == [0, 1, 2]
    assert queue.next_decode() is None


def test_next_decode_respects_inflight_limit():
    queue = world(max_inflight=2)
    for sector in (0, 1, 2):
        queue.enqueue(sector)
    assert queue.next_decode() == 0
    assert queue.next_decode() == 1
    assert queue.next_decode() is None
    assert queue.budget()["inflight"] == 2


def test_mark_ready_moves_inflight_sector():
    queue = world()
    queue.enqueue(0)
    assert queue.mark_ready(0, 128) is False
    assert queue.next_decode() == 0
    assert queue.mark_ready(0, 128) is True
    assert queue.ready() == [0]
    assert queue.mark_ready(0, 128) is False
    assert queue.budget()["inflight"] == 0


def test_ready_table_evicts_oldest_entry():
    queue = world(max_ready=2)
    for sector in (0, 1, 2):
        queue.enqueue(sector)
        queue.next_decode()
        assert queue.mark_ready(sector, 64) is True
    assert queue.ready() == [1, 2]
    assert queue.budget()["ready"] == 2


def test_cancel_stale_drops_queued_requests():
    queue = world()
    for sector in (0, 1, 2):
        queue.enqueue(sector)
    assert queue.cancel_stale({2}) == [0, 1]
    assert queue.budget()["queued"] == 1
    assert queue.enqueue(0) is True


def test_upload_consumes_frame_budget():
    queue = world()
    queue.enqueue(0)
    queue.next_decode()
    queue.mark_ready(0, 100)
    queue.begin_frame()
    assert queue.upload(0) == 100
    assert queue.ready() == []
    assert queue.budget()["upload_remaining"] == 4096 - 100
    with pytest.raises(tq.TextureError) as exc:
        queue.upload(0)
    assert exc.value.code == "TEXTURE_NOT_READY"


def test_snapshot_restore_roundtrip():
    queue = world()
    queue.enqueue(0)
    queue.enqueue(1)
    queue.next_decode()
    queue.mark_ready(0, 64)
    queue.enqueue(2)
    state = queue.snapshot()
    clone = tq.TexturePrefetchQueue()
    clone.restore(state)
    assert clone.snapshot() == state
    assert clone.ready() == queue.ready()
    assert clone.budget() == queue.budget()
    assert clone.predict_sectors() == queue.predict_sectors()


def test_restore_rejects_malformed_state():
    queue = world()
    state = queue.snapshot()
    bad_states = [None, {}, dict(state, version=7),
                  {key: value for key, value in state.items() if key != "queue"},
                  dict(state, world="twelve")]
    for bad in bad_states:
        with pytest.raises(tq.TextureError) as exc:
            queue.restore(bad)
        assert exc.value.code == "TEXTURE_SNAPSHOT_INVALID"
    assert queue.budget()["queued"] == 0


def test_concurrent_enqueue_has_single_winner():
    queue = world()
    results = []
    errors = []
    barrier = threading.Barrier(8)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(queue.enqueue(4))
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert results.count(True) == 1
    assert results.count(False) == 7
