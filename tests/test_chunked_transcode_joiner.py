import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import chunked_transcode_joiner as cj


def build(**kwargs):
    params = {
        "max_chunk_frames": 300,
        "max_chunks": 64,
        "max_retries": 2,
        "max_total_retries": 5,
    }
    params.update(kwargs)
    return cj.ChunkedTranscodeJoiner(**params)


def report(index, start, end, params="p1", intact=True):
    return cj.ChunkReport(
        index=index,
        start_frame=start,
        end_frame=end,
        params_hash=params,
        keyframe_intact=intact,
    )


def test_plan_chunks_splits_at_keyframes_and_forced_max():
    joiner = build()
    specs = joiner.plan_chunks(1000, [0, 400, 700])
    assert [(s.index, s.start_frame, s.end_frame, s.key_aligned) for s in specs] == [
        (0, 0, 300, True),
        (1, 300, 400, False),
        (2, 400, 700, True),
        (3, 700, 1000, True),
    ]
    assert joiner.chunks() == specs


def test_plan_rejects_bad_duration_and_keyframes():
    joiner = build()
    with pytest.raises(cj.ChunkError) as info:
        joiner.plan_chunks(0, [0])
    assert info.value.code == "INVALID_DURATION"
    for keyframes in ([100, 300], [0, 300, 200], [0, 1000], [0, 0, 200]):
        with pytest.raises(cj.ChunkError) as info:
            joiner.plan_chunks(1000, keyframes)
        assert info.value.code == "INVALID_KEYFRAMES"


def test_plan_respects_chunk_cap():
    joiner = build(max_chunks=3)
    with pytest.raises(cj.ChunkError) as info:
        joiner.plan_chunks(1000, [0, 100, 200, 300])
    assert info.value.code == "CHUNK_LIMIT"
    assert joiner.chunks() == []


def test_replan_locked_after_any_report():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    joiner.mark_done(report(0, 0, 300))
    with pytest.raises(cj.ChunkError) as info:
        joiner.plan_chunks(600, [0, 200])
    assert info.value.code == "PLAN_LOCKED"
    assert len(joiner.chunks()) == 2


def test_mark_done_unknown_index_and_span_mismatch():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    with pytest.raises(cj.ChunkError) as info:
        joiner.mark_done(report(5, 0, 300))
    assert info.value.code == "UNKNOWN_CHUNK"
    with pytest.raises(cj.ChunkError) as info:
        joiner.mark_done(report(0, 0, 299))
    assert info.value.code == "SPAN_MISMATCH"
    assert joiner.chunk_state(0) == "needs_retry"


def test_mark_done_accepts_out_of_order_reports():
    joiner = build()
    joiner.plan_chunks(900, [0, 300, 600])
    assert joiner.mark_done(report(2, 600, 900)) == "done"
    assert joiner.chunk_state(2) == "done"
    assert joiner.contiguous_done() == 0
    joiner.mark_done(report(0, 0, 300))
    assert joiner.contiguous_done() == 1
    joiner.mark_done(report(1, 300, 600))
    assert joiner.contiguous_done() == 3
    progress = joiner.progress()
    assert progress["planned"] == 3
    assert progress["done"] == 3
    assert progress["pending"] == 0
    assert progress["needs_retry"] == 0
    assert progress["failed"] == 0


def test_mark_done_duplicate_report_is_idempotent():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    assert joiner.mark_done(report(0, 0, 300)) == "done"
    assert joiner.mark_done(report(0, 0, 300)) == "done"
    stats = joiner.stats()
    assert stats["reports"] == 1
    assert stats["duplicate_reports"] == 1
    with pytest.raises(cj.ChunkError) as info:
        joiner.mark_done(report(0, 0, 300, params="p2"))
    assert info.value.code == "ALREADY_DONE"


def test_params_hash_mismatch_rejected():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    joiner.mark_done(report(0, 0, 300, params="p1"))
    with pytest.raises(cj.ChunkError) as info:
        joiner.mark_done(report(1, 300, 600, params="p2"))
    assert info.value.code == "PARAMS_MISMATCH"
    assert joiner.chunk_state(1) == "pending"
    assert joiner.progress()["done"] == 1


def test_keyframe_integrity_only_mandatory_for_key_aligned_chunks():
    joiner = build()
    joiner.plan_chunks(1000, [0, 400, 700])
    with pytest.raises(cj.ChunkError) as info:
        joiner.mark_done(report(0, 0, 300, intact=False))
    assert info.value.code == "KEYFRAME_BROKEN"
    assert joiner.chunk_state(0) == "needs_retry"
    assert joiner.mark_done(report(0, 0, 300, intact=True)) == "done"
    assert joiner.mark_done(report(1, 300, 400, intact=False)) == "done"


def test_retry_chunk_exhausts_per_chunk_limit():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    with pytest.raises(cj.ChunkError):
        joiner.mark_done(report(0, 0, 300, intact=False))
    assert joiner.retry_chunk(0) == 1
    assert joiner.retry_chunk(0) == 2
    with pytest.raises(cj.ChunkError) as info:
        joiner.retry_chunk(0)
    assert info.value.code == "RETRY_EXHAUSTED"
    assert joiner.chunk_state(0) == "failed"


def test_retry_chunk_respects_total_budget():
    joiner = build(max_total_retries=2)
    joiner.plan_chunks(600, [0, 300])
    for index in (0, 1):
        with pytest.raises(cj.ChunkError):
            joiner.mark_done(report(index, index * 300, (index + 1) * 300, intact=False))
    assert joiner.retry_chunk(0) == 1
    assert joiner.retry_chunk(0) == 2
    with pytest.raises(cj.ChunkError) as info:
        joiner.retry_chunk(1)
    assert info.value.code == "RETRY_BUDGET"
    with pytest.raises(cj.ChunkError) as info:
        joiner.retry_chunk(9)
    assert info.value.code == "UNKNOWN_CHUNK"


def test_join_requires_readiness_and_totals_frames():
    joiner = build()
    with pytest.raises(cj.ChunkError) as info:
        joiner.join()
    assert info.value.code == "NOT_PLANNED"
    joiner.plan_chunks(600, [0, 300])
    joiner.mark_done(report(0, 0, 300))
    with pytest.raises(cj.ChunkError) as info:
        joiner.join()
    assert info.value.code == "NOT_READY"
    joiner.mark_done(report(1, 300, 600))
    result = joiner.join()
    assert result.complete is True
    assert result.chunk_count == 2
    assert result.joined_frames == 600
    assert result.truncated_frames == 0
    assert result.reasons == ()
    assert joiner.stats()["joins"] == 1


def test_verify_boundary_detects_gap_and_join_refuses():
    joiner = build()
    joiner.plan_chunks(600, [0, 300])
    joiner.mark_done(report(0, 0, 300))
    joiner.mark_done(report(1, 303, 600))
    assert joiner.verify_boundary(0) is True
    assert joiner.verify_boundary(1) is False
    with pytest.raises(cj.ChunkError) as info:
        joiner.join()
    assert info.value.code == "BOUNDARY_MISMATCH"
    assert joiner.chunk_state(1) == "needs_retry"


def test_snapshot_restore_round_trip_and_invalid_state():
    joiner = build()
    joiner.plan_chunks(900, [0, 300, 600])
    joiner.mark_done(report(0, 0, 300))
    saved = joiner.snapshot()
    joiner.mark_done(report(1, 300, 600))
    joiner.mark_done(report(2, 600, 900))
    assert joiner.progress()["done"] == 3
    joiner.restore(saved)
    assert joiner.progress()["done"] == 1
    assert [spec.end_frame for spec in joiner.chunks()] == [300, 600, 900]
    assert joiner.stats()["restores"] == 1
    with pytest.raises(cj.ChunkError) as info:
        joiner.restore({"plan": []})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert joiner.progress()["done"] == 1


def test_concurrent_reports_are_serialized_and_idempotent():
    joiner = build()
    joiner.plan_chunks(800, [0, 100, 200, 300, 400, 500, 600, 700])
    reports = [report(i, i * 100, (i + 1) * 100) for i in range(8)]
    barrier = threading.Barrier(8)

    def submit(item):
        barrier.wait()
        return joiner.mark_done(item)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, reports))
    assert results == ["done"] * 8
    progress = joiner.progress()
    assert progress["done"] == 8
    assert progress["pending"] == 0
    assert joiner.contiguous_done() == 8
    assert joiner.stats()["retries"] == 0
