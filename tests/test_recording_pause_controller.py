import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import recording_pause_controller as rc


def build(**kwargs):
    params = {
        "fps": 30,
        "max_pauses": 8,
        "max_segment_frames": 300,
        "max_segments": 16,
        "min_segment_frames": 1,
        "transition_frames": 0,
        "bytes_per_frame": 4,
    }
    params.update(kwargs)
    return rc.RecordingPauseController(**params)


def manifest_entry(index, start, frames, *, closed=True, bytes_=None):
    return {
        "index": index,
        "start_frame": start,
        "end_frame": start + frames,
        "frames": frames,
        "transition_frames": 0,
        "closed": closed,
        "bytes": frames * 4 if bytes_ is None else bytes_,
    }


def test_start_transitions_from_idle_only_once():
    controller = build()
    assert controller.state() == "idle"
    controller.start()
    assert controller.state() == "recording"
    assert controller.media_time() == 0
    with pytest.raises(rc.RecordingError) as info:
        controller.start()
    assert info.value.code == "STATE_ERROR"


def test_pause_and_resume_are_idempotent():
    controller = build()
    controller.start()
    controller.pause()
    controller.pause()
    status = controller.status()
    assert status["state"] == "paused"
    assert status["pauses"] == 1
    controller.resume()
    controller.resume()
    status = controller.status()
    assert status["state"] == "recording"
    assert status["pauses"] == 1


def test_resume_and_stop_reject_invalid_states():
    controller = build()
    with pytest.raises(rc.RecordingError) as info:
        controller.resume()
    assert info.value.code == "STATE_ERROR"
    with pytest.raises(rc.RecordingError) as info:
        controller.stop()
    assert info.value.code == "STATE_ERROR"
    controller.start()
    controller.stop()
    with pytest.raises(rc.RecordingError) as info:
        controller.resume()
    assert info.value.code == "STATE_ERROR"
    with pytest.raises(rc.RecordingError) as info:
        controller.stop()
    assert info.value.code == "STATE_ERROR"


def test_pause_limit_is_enforced():
    controller = build(max_pauses=2)
    controller.start()
    controller.pause()
    controller.resume()
    controller.pause()
    controller.resume()
    with pytest.raises(rc.RecordingError) as info:
        controller.pause()
    assert info.value.code == "PAUSE_LIMIT"
    assert controller.state() == "recording"
    assert controller.status()["pauses"] == 2


def test_write_frame_advances_media_and_open_segment():
    controller = build()
    controller.start()
    assert controller.write_frame(0) is True
    assert controller.write_frame(33) is True
    assert controller.write_frame(66) is True
    assert controller.media_time() == 3
    status = controller.status()
    assert status["written_frames"] == 3
    assert status["dropped_frames"] == 0
    segments = controller.segments()
    assert len(segments) == 1
    assert (segments[0].index, segments[0].start_frame, segments[0].end_frame) == (0, 0, 3)
    assert segments[0].frames == 3
    assert segments[0].state == "open"


def test_segment_rotates_at_max_frames():
    controller = build(max_segment_frames=5)
    controller.start()
    for wall_ms in range(7):
        controller.write_frame(wall_ms * 33)
    segments = controller.segments()
    assert len(segments) == 2
    assert (segments[0].start_frame, segments[0].end_frame, segments[0].frames) == (0, 5, 5)
    assert segments[0].state == "complete"
    assert (segments[1].start_frame, segments[1].end_frame, segments[1].frames) == (5, 7, 2)
    assert segments[1].state == "open"
    assert controller.media_time() == 7


def test_segment_limit_stops_writing_without_advancing_media():
    controller = build(max_segment_frames=2, max_segments=2)
    controller.start()
    for wall_ms in range(4):
        assert controller.write_frame(wall_ms * 33) is True
    with pytest.raises(rc.RecordingError) as info:
        controller.write_frame(132)
    assert info.value.code == "SEGMENT_LIMIT"
    assert controller.media_time() == 4
    assert len(controller.segments()) == 2


def test_stop_closes_the_open_segment():
    controller = build()
    controller.start()
    for wall_ms in range(3):
        controller.write_frame(wall_ms * 33)
    controller.stop()
    assert controller.state() == "stopped"
    segments = controller.segments()
    assert len(segments) == 1
    assert segments[0].state == "complete"
    assert segments[0].end_frame == 3
    with pytest.raises(rc.RecordingError) as info:
        controller.stop()
    assert info.value.code == "STATE_ERROR"


def test_resume_transition_inserts_a_media_gap():
    controller = build()
    controller.start()
    for wall_ms in range(3):
        controller.write_frame(wall_ms * 33)
    controller.pause()
    controller.resume(transition_frames=8)
    assert controller.media_time() == 11
    controller.write_frame(5000)
    segments = controller.segments()
    assert len(segments) == 2
    assert (segments[0].start_frame, segments[0].end_frame) == (0, 3)
    assert (segments[1].start_frame, segments[1].end_frame) == (11, 12)
    assert segments[1].frames == 1
    assert segments[1].transition_frames == 8


def test_status_reports_counters_after_pause_cycle():
    controller = build()
    controller.start()
    for wall_ms in range(3):
        controller.write_frame(wall_ms * 33)
    controller.pause()
    controller.resume()
    controller.write_frame(4000)
    controller.write_frame(4033)
    status = controller.status()
    assert status["state"] == "recording"
    assert status["written_frames"] == 5
    assert status["dropped_frames"] == 0
    assert status["pauses"] == 1
    assert status["media_frame"] == 5
    assert status["segments"] == 2
    assert controller.segments()[1].start_frame == 3


def test_recover_accepts_a_clean_closed_manifest():
    controller = build()
    manifest = [manifest_entry(0, 0, 3), manifest_entry(1, 3, 3)]
    controller.recover(manifest)
    assert controller.state() == "paused"
    assert controller.media_time() == 6
    segments = controller.segments()
    assert [segment.index for segment in segments] == [0, 1]
    assert all(segment.state == "complete" for segment in segments)
    assert [segment.frames for segment in segments] == [3, 3]
    with pytest.raises(rc.RecordingError) as info:
        controller.recover(manifest)
    assert info.value.code == "STATE_ERROR"


def test_recover_rejects_corrupt_manifests_atomically():
    controller = build(max_segments=2)
    bad_bytes = [manifest_entry(0, 0, 3), manifest_entry(1, 3, 3, bytes_=11)]
    with pytest.raises(rc.RecordingError) as info:
        controller.recover(bad_bytes)
    assert info.value.code == "CORRUPT_MANIFEST"
    assert controller.state() == "idle"
    overlap = [manifest_entry(0, 0, 3), manifest_entry(1, 2, 3)]
    with pytest.raises(rc.RecordingError) as info:
        controller.recover(overlap)
    assert info.value.code == "CORRUPT_MANIFEST"
    too_many = [manifest_entry(0, 0, 3), manifest_entry(1, 3, 3), manifest_entry(2, 6, 3)]
    with pytest.raises(rc.RecordingError) as info:
        controller.recover(too_many)
    assert info.value.code == "SEGMENT_LIMIT"
    assert controller.state() == "idle"
    assert controller.segments() == []


def test_snapshot_restore_round_trip_and_invalid_state():
    controller = build()
    controller.start()
    for wall_ms in range(4):
        controller.write_frame(wall_ms * 33)
    controller.pause()
    controller.resume()
    controller.write_frame(5000)
    controller.write_frame(5033)
    saved = controller.status()
    snapshot = controller.snapshot()
    assert saved["media_frame"] == 6
    assert saved["segments"] == 2
    for wall_ms in range(3):
        controller.write_frame(6000 + wall_ms * 33)
    assert controller.media_time() == 9
    controller.restore(snapshot)
    assert controller.status() == saved
    assert controller.segments()[0].state == "complete"
    assert controller.segments()[1].state == "open"
    with pytest.raises(rc.RecordingError) as info:
        controller.restore({"state": "recording"})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert controller.media_time() == 6


def test_concurrent_pause_performs_a_single_transition():
    controller = build()
    controller.start()
    barrier = threading.Barrier(8)

    def pause_once(_):
        barrier.wait()
        controller.pause()
        return True

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(pause_once, range(8)))
    assert results == [True] * 8
    assert controller.state() == "paused"
    assert controller.status()["pauses"] == 1


def test_concurrent_writes_are_serialized():
    controller = build(max_segment_frames=10000)
    controller.start()
    barrier = threading.Barrier(4)

    def write_batch(worker):
        barrier.wait()
        for step in range(25):
            controller.write_frame(worker * 1000 + step)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write_batch, range(4)))
    status = controller.status()
    assert status["written_frames"] == 100
    assert status["media_frame"] == 100
    assert status["segments"] == 1
    assert controller.segments()[0].frames == 100
