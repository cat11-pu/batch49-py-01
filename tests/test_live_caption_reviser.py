import copy
import threading

import pytest

import live_caption_reviser as lc


def reviser(**kwargs):
    return lc.LiveCaptionReviser(**kwargs)


def test_first_hypothesis_stays_pending():
    r = reviser()
    out = r.push_hypothesis("s1", "hello world", now_ms=0)
    assert out["status"] == "accepted"
    assert out["pending"] == "hello world"
    assert out["revisions"] == 1
    assert r.text("s1") == {"committed": "", "pending": "hello world",
                            "display": "hello world"}
    assert r.stats() == {"hypotheses": 1, "revisions": 1, "commits": 0,
                         "duplicates": 0, "rejected": 0}


def test_growing_hypothesis_replaces_the_pending_tail():
    r = reviser()
    r.push_hypothesis("s1", "alpha beta", now_ms=0)
    out = r.push_hypothesis("s1", "alpha beta gamma", now_ms=100)
    assert out["revisions"] == 2
    assert r.text("s1") == {"committed": "", "pending": "alpha beta gamma",
                            "display": "alpha beta gamma"}
    assert r.stats()["revisions"] == 2


def test_stabilize_waits_until_the_window_elapses():
    r = reviser(window_ms=400)
    r.push_hypothesis("s1", "one", now_ms=0)
    r.push_hypothesis("s1", "one two", now_ms=100)

    out = r.stabilize("s1", now_ms=300)
    assert out["status"] == "waiting"
    assert out["remaining_ms"] == 200
    assert r.text("s1")["committed"] == ""
    assert r.window("s1") == 400
    assert r.latency("s1", now_ms=300) == 200


def test_stabilize_commits_once_the_window_has_passed():
    r = reviser(window_ms=400)
    r.push_hypothesis("s1", "one", now_ms=0)
    r.push_hypothesis("s1", "one two three", now_ms=100)

    out = r.stabilize("s1", now_ms=500)
    assert out["status"] == "committed"
    assert out["committed"] == "one two three"
    assert r.text("s1") == {"committed": "one two three", "pending": "",
                            "display": "one two three"}
    assert r.stats()["commits"] == 1


def test_commit_forces_the_pending_tail_out():
    r = reviser(window_ms=5000)
    r.push_hypothesis("s1", "forced now", now_ms=0)

    out = r.commit("s1")
    assert out["status"] == "committed"
    assert out["committed"] == "forced now"
    assert out["committed_tokens"] == 2
    assert r.text("s1")["pending"] == ""
    assert r.stats()["commits"] == 1


def test_revise_replaces_only_the_pending_tail():
    r = reviser()
    r.push_hypothesis("s1", "one two", now_ms=0)
    r.commit("s1")
    r.push_hypothesis("s1", "one two three", now_ms=10)

    out = r.revise("s1", "one two 3")
    assert out["status"] == "revised"
    assert out["pending"] == "3"
    assert r.text("s1") == {"committed": "one two", "pending": "3",
                            "display": "one two 3"}


def test_revise_cannot_touch_the_committed_prefix():
    r = reviser()
    r.push_hypothesis("s1", "one two", now_ms=0)
    r.commit("s1")

    with pytest.raises(lc.CaptionError) as exc:
        r.revise("s1", "one")
    assert exc.value.code == "CAPTION_COMMITTED_IMMUTABLE"
    assert r.text("s1")["committed"] == "one two"


def test_text_shape_and_display_join():
    r = reviser()
    r.push_hypothesis("s1", "aa bb", now_ms=0)
    state = r.text("s1")
    assert set(state) == {"committed", "pending", "display"}
    assert state["display"] == "aa bb"
    assert state["committed"] == ""

    r.commit("s1")
    r.push_hypothesis("s1", "aa bb cc", now_ms=5)
    after = r.text("s1")
    assert after["committed"] == "aa bb"
    assert after["pending"] == "cc"
    assert after["display"] == "aa bb cc"


def test_pending_character_limit():
    r = reviser(max_pending_chars=8)
    with pytest.raises(lc.CaptionError) as exc:
        r.push_hypothesis("s1", "aaaa bbbb cccc", now_ms=0)
    assert exc.value.code == "CAPTION_PENDING_OVERFLOW"
    assert r.stats()["rejected"] == 1
    assert r.stats()["revisions"] == 0


def test_revision_limit():
    r = reviser(max_revisions=3)
    r.push_hypothesis("s1", "a", now_ms=0)
    r.push_hypothesis("s1", "a b", now_ms=1)
    r.push_hypothesis("s1", "a b c", now_ms=2)

    with pytest.raises(lc.CaptionError) as exc:
        r.push_hypothesis("s1", "a b c d", now_ms=3)
    assert exc.value.code == "CAPTION_REVISION_LIMIT"
    assert r.stats()["revisions"] == 3


def test_stream_capacity_and_sorted_listing():
    r = reviser(max_streams=2)
    r.push_hypothesis("s2", "second", now_ms=0)
    r.push_hypothesis("s1", "first", now_ms=0)
    assert r.streams() == ["s1", "s2"]

    with pytest.raises(lc.CaptionError) as exc:
        r.push_hypothesis("s3", "third", now_ms=0)
    assert exc.value.code == "CAPTION_CAPACITY_EXCEEDED"


def test_invalid_arguments_and_unknown_stream():
    r = reviser()
    with pytest.raises(lc.CaptionError) as empty:
        r.push_hypothesis("", "text", now_ms=0)
    assert empty.value.code == "CAPTION_ARGUMENT_INVALID"

    with pytest.raises(lc.CaptionError) as not_text:
        r.push_hypothesis("s1", 17, now_ms=0)
    assert not_text.value.code == "CAPTION_ARGUMENT_INVALID"

    with pytest.raises(lc.CaptionError) as missing:
        r.stabilize("ghost", now_ms=0)
    assert missing.value.code == "CAPTION_STREAM_MISSING"

    with pytest.raises(lc.CaptionError) as missing_text:
        r.text("ghost")
    assert missing_text.value.code == "CAPTION_STREAM_MISSING"


def test_streams_keep_independent_state():
    r = reviser()
    r.push_hypothesis("a", "alpha", now_ms=0)
    r.push_hypothesis("b", "bravo", now_ms=0)
    r.commit("a")

    assert r.text("a") == {"committed": "alpha", "pending": "",
                           "display": "alpha"}
    assert r.text("b") == {"committed": "", "pending": "bravo",
                           "display": "bravo"}
    assert r.stats()["commits"] == 1


def test_snapshot_is_detached_and_restore_roundtrips():
    r = reviser(window_ms=300)
    r.push_hypothesis("s1", "keep me", now_ms=0)
    r.commit("s1")
    r.push_hypothesis("s1", "keep me too", now_ms=10)

    state = r.snapshot()
    pristine = copy.deepcopy(state)
    r.commit("s1")
    assert r.text("s1")["committed"] == "keep me too"

    clone = reviser(window_ms=300)
    clone.restore(pristine)
    assert clone.text("s1") == {"committed": "keep me", "pending": "too",
                                "display": "keep me too"}
    assert clone.streams() == ["s1"]
    assert clone.stats()["commits"] == 1

    for bad in (None, {}, [], "state"):
        with pytest.raises(lc.CaptionError) as exc:
            clone.restore(bad)
        assert exc.value.code == "CAPTION_SNAPSHOT_INVALID"
    assert clone.text("s1")["committed"] == "keep me"


def test_concurrent_streams_stay_independent():
    r = reviser(max_streams=8)
    names = [f"s{index}" for index in range(6)]
    errors = []
    barrier = threading.Barrier(len(names))

    def worker(stream_id):
        try:
            barrier.wait(timeout=5)
            r.push_hypothesis(stream_id, f"text {stream_id}", now_ms=0)
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(name,)) for name in names]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert r.streams() == names
    for name in names:
        assert r.text(name)["pending"] == f"text {name}"
    assert r.stats()["hypotheses"] == 6
