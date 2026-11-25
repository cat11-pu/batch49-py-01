import copy
import threading
import time

import pytest

import ll_hls_part_publisher as hp


def publisher(**kwargs):
    kwargs.setdefault("hold_back_parts", 0)
    return hp.LlHlsPartPublisher(**kwargs)


def cut(p, media_seq, count=1, duration_ms=500):
    parts = []
    for index in range(count):
        p.push_chunk(bytes([65 + index]) * 4, duration_ms)
        parts.append(p.cut_part(media_seq))
    return parts


def test_push_chunk_rejects_invalid_payload():
    p = publisher()
    bad_calls = [(b"", 500), (b"x", 0), (b"x", -1), ("text", 500)]
    for data, duration_ms in bad_calls:
        with pytest.raises(hp.PartPublishError) as exc:
            p.push_chunk(data, duration_ms)
        assert exc.value.code == "PART_INVALID"


def test_push_chunk_accumulates_buffered_duration():
    p = publisher()
    assert p.push_chunk(b"aa", 200) == 200
    assert p.push_chunk(b"bbb", 300) == 500


def test_cut_part_requires_minimum_duration():
    p = publisher()
    p.push_chunk(b"aa", 100)
    assert p.cut_part(0) is None
    assert p.pending_count() == 0
    p.push_chunk(b"bb", 100)
    assert p.cut_part(0) == (0, 0)
    assert p.pending_count() == 1
    view = p.publish(0)
    assert view["parts"][0]["duration_ms"] == 200


def test_part_index_increments_and_resets_per_media_seq():
    p = publisher()
    assert cut(p, 0, 2) == [(0, 0), (0, 1)]
    assert cut(p, 1) == [(1, 0)]
    assert [part["part_index"] for part in p.publish(1)["parts"]] == [0, 1, 0]


def test_publish_flags_gap_and_rejects_backwards_media_seq():
    p = publisher()
    cut(p, 0)
    first = p.publish(0)
    assert first["gap"] is False
    assert first["version"] == 1
    jumped = p.publish(3)
    assert jumped["gap"] is True
    assert jumped["version"] == 2
    with pytest.raises(hp.PartPublishError) as exc:
        p.publish(1)
    assert exc.value.code == "MEDIA_SEQ_INVALID"


def test_publish_version_only_advances_on_change():
    p = publisher()
    cut(p, 0)
    version = p.publish(0)["version"]
    assert p.publish(0)["version"] == version
    cut(p, 0)
    assert p.publish(0)["version"] == version + 1


def test_hold_back_hides_latest_parts():
    p = publisher(hold_back_parts=1)
    cut(p, 0, 3)
    view = p.publish(0)
    assert [part["part_index"] for part in view["parts"]] == [0, 1]
    cut(p, 0)
    assert [part["part_index"] for part in p.publish(0)["parts"]] == [0, 1, 2]


def test_playlist_window_keeps_newest_parts():
    p = publisher(window_parts=2)
    cut(p, 0, 4)
    view = p.publish(0)
    assert [part["part_index"] for part in view["parts"]] == [2, 3]


def test_preload_hint_tracks_next_part():
    p = publisher()
    p.push_chunk(b"abcd", 500)
    assert p.preload_hint(0) == {"media_seq": 0, "part_index": 0, "duration_ms": 500}
    assert p.cut_part(0) == (0, 0)
    assert p.preload_hint(0) is None
    p.push_chunk(b"efgh", 500)
    assert p.preload_hint(0) == {"media_seq": 0, "part_index": 1, "duration_ms": 500}


def test_blocking_reload_returns_immediately_for_newer_version():
    p = publisher()
    version = p.publish(0)["version"]
    view = p.blocking_reload(0, timeout_ms=100)
    assert view["version"] == version
    assert "timeout" not in view


def test_blocking_reload_waits_for_next_publish():
    p = publisher()
    cut(p, 0)
    version = p.publish(0)["version"]
    result = {}

    def worker():
        result.update(p.blocking_reload(version, timeout_ms=5000))

    thread = threading.Thread(target=worker)
    thread.start()
    time.sleep(0.05)
    cut(p, 0)
    p.publish(0)
    thread.join(5)

    assert result["version"] == version + 1
    assert [part["part_index"] for part in result["parts"]] == [0, 1]


def test_snapshot_is_detached_and_restores():
    p = publisher()
    cut(p, 0, 2)
    p.publish(0)
    snap = p.snapshot()
    assert set(snap) == {"version", "published", "pending", "buffer", "counters"}
    state = copy.deepcopy(snap)
    clone = publisher()
    clone.restore(state)
    assert clone.playlist() == p.playlist()
    snap["published"].clear()
    assert p.playlist()["parts"]
    assert p.stats()["parts"] == 2


def test_restore_rejects_malformed_state():
    p = publisher()
    cut(p, 0)
    bad_states = [
        None,
        {},
        {"version": 1, "published": {}, "pending": [], "buffer": []},
        {
            "version": 1,
            "published": [],
            "pending": [],
            "buffer": [],
            "counters": {},
        },
        {
            "version": 1,
            "published": {},
            "pending": "nope",
            "buffer": [],
            "counters": {},
        },
    ]
    for state in bad_states:
        with pytest.raises(hp.PartPublishError) as exc:
            p.restore(state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert p.pending_count() == 1


def test_concurrent_readers_see_complete_playlist_views():
    p = publisher()
    cut(p, 0)
    p.publish(0)
    views = []
    errors = []

    def writer():
        for _ in range(20):
            cut(p, 0)
            p.publish(0)

    def reader():
        try:
            for _ in range(20):
                view = p.blocking_reload(-1, timeout_ms=5000)
                views.append(view)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=reader) for _ in range(4)]
    writer_thread = threading.Thread(target=writer)
    for thread in threads:
        thread.start()
    writer_thread.start()
    for thread in threads:
        thread.join()
    writer_thread.join()

    assert errors == []
    assert len(views) == 80
    for view in views:
        indices = [part["part_index"] for part in view["parts"]]
        assert indices == sorted(indices)
        assert all(part["duration_ms"] == 500 for part in view["parts"])
    assert p.stats()["published"] == 21
    assert p.stats()["parts"] == 21


def test_stats_counts_chunks_parts_and_reloads():
    p = publisher()
    p.push_chunk(b"aaaa", 500)
    p.push_chunk(b"bbbb", 500)
    p.cut_part(0)
    version = p.publish(0)["version"]
    p.blocking_reload(version)
    p.blocking_reload(0)
    assert p.stats() == {
        "chunks": 2,
        "parts": 1,
        "published": 1,
        "reloads": 2,
        "timeouts": 0,
    }
