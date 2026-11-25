import copy
import threading

import pytest

import parameter_set_cache as ps


def cache(**kwargs):
    return ps.ParameterSetCache(**kwargs)


def test_put_returns_version_and_marks_first_change():
    c = cache()
    out = c.put("sps", 3, b"\x10\x11")
    assert set(out) == {"kind", "ps_id", "version", "changed"}
    assert out == {"kind": "sps", "ps_id": 3, "version": 1, "changed": True}
    assert c.stats() == {"puts": 1, "changes": 1, "activations": 0,
                         "pending": 0, "invalidated": 0, "rejected": 0}


def test_put_same_payload_is_idempotent():
    c = cache()
    c.put("pps", 1, b"\xaa")
    again = c.put("pps", 1, b"\xaa")
    assert again["changed"] is False
    assert again["version"] == 1
    assert c.stats()["puts"] == 2
    assert c.stats()["changes"] == 1

    changed = c.put("pps", 1, b"\xbb")
    assert changed["changed"] is True
    assert changed["version"] == 2


def test_put_rejects_unknown_kind_and_bad_id():
    c = cache()
    cases = [
        ("sei", 0, b"\x01", "PS_KIND_UNKNOWN"),
        ("sps", 32, b"\x01", "PS_ID_OUT_OF_RANGE"),
        ("sps", -1, b"\x01", "PS_ID_OUT_OF_RANGE"),
        ("pps", 256, b"\x01", "PS_ID_OUT_OF_RANGE"),
        ("vps", 16, b"\x01", "PS_ID_OUT_OF_RANGE"),
        ("sps", True, b"\x01", "PS_ARGUMENT_INVALID"),
        ("sps", "3", b"\x01", "PS_ARGUMENT_INVALID"),
    ]
    for kind, ps_id, payload, code in cases:
        with pytest.raises(ps.ParameterSetError) as exc:
            c.put(kind, ps_id, payload)
        assert exc.value.code == code
    assert c.stats()["rejected"] == len(cases)
    assert c.entries("sps") == []


def test_put_rejects_empty_and_oversized_payload():
    c = cache(max_payload_bytes=8)
    with pytest.raises(ps.ParameterSetError) as empty:
        c.put("sps", 0, b"")
    assert empty.value.code == "PS_PAYLOAD_EMPTY"

    with pytest.raises(ps.ParameterSetError) as big:
        c.put("sps", 0, b"\x00" * 9)
    assert big.value.code == "PS_PAYLOAD_TOO_LARGE"

    with pytest.raises(ps.ParameterSetError) as text:
        c.put("sps", 0, "payload")
    assert text.value.code == "PS_ARGUMENT_INVALID"
    assert c.entries("sps") == []


def test_get_latest_and_pinned_version():
    c = cache()
    c.put("sps", 4, b"\x01")
    c.put("sps", 4, b"\x02")

    latest = c.get("sps", 4)
    assert latest["version"] == 2
    assert latest["payload"] == b"\x02"

    old = c.get("sps", 4, version=1)
    assert old["version"] == 1
    assert old["payload"] == b"\x01"

    with pytest.raises(ps.ParameterSetError) as missing_version:
        c.get("sps", 4, version=8)
    assert missing_version.value.code == "PS_VERSION_MISSING"

    with pytest.raises(ps.ParameterSetError) as missing_id:
        c.get("pps", 9)
    assert missing_id.value.code == "PS_ID_MISSING"


def test_versions_beyond_retention_are_dropped():
    c = cache(max_versions=2)
    c.put("vps", 1, b"\x01")
    c.put("vps", 1, b"\x02")
    c.put("vps", 1, b"\x03")

    assert c.get("vps", 1)["version"] == 3
    assert c.get("vps", 1, version=2)["payload"] == b"\x02"

    with pytest.raises(ps.ParameterSetError) as gone:
        c.get("vps", 1, version=1)
    assert gone.value.code == "PS_VERSION_MISSING"


def test_capacity_per_kind_allows_overwrite():
    c = cache(max_entries_per_kind=2)
    c.put("pps", 0, b"\x01")
    c.put("pps", 1, b"\x01")

    with pytest.raises(ps.ParameterSetError) as full:
        c.put("pps", 2, b"\x01")
    assert full.value.code == "PS_CACHE_FULL"

    overwritten = c.put("pps", 0, b"\x02")
    assert overwritten["version"] == 2
    assert c.entries("pps") == [
        {"ps_id": 0, "version": 2, "size": 1},
        {"ps_id": 1, "version": 1, "size": 1},
    ]


def test_first_activation_applies_immediately():
    c = cache()
    c.put("sps", 2, b"\x0a")

    out = c.activate("stream-a", "sps", 2)
    assert out["applied"] is True
    assert out["version"] == 1

    frame = c.bind_frame("stream-a")
    assert frame["keyframe"] is False
    assert frame["active"] == {"sps": {"ps_id": 2, "version": 1}}
    assert frame["generation"] == 1


def test_second_activation_waits_for_keyframe():
    c = cache()
    c.put("sps", 2, b"\x0a")
    c.put("sps", 7, b"\x0b")
    c.activate("stream-a", "sps", 2)

    out = c.activate("stream-a", "sps", 7)
    assert out["applied"] is False

    plain = c.bind_frame("stream-a")
    assert plain["active"]["sps"]["ps_id"] == 2
    assert plain["generation"] == 1

    key = c.bind_frame("stream-a", keyframe=True)
    assert key["keyframe"] is True
    assert key["active"]["sps"]["ps_id"] == 7
    assert key["generation"] == 2
    assert c.generation("stream-a") == 2


def test_bind_frame_returns_detached_active_map():
    c = cache()
    c.put("sps", 2, b"\x0a")
    c.activate("s", "sps", 2)

    frame = c.bind_frame("s")
    frame["active"]["sps"]["ps_id"] = 99
    assert c.bind_frame("s")["active"]["sps"]["ps_id"] == 2


def test_entries_sorted_and_kind_scoped():
    c = cache()
    c.put("sps", 7, b"\x01\x02")
    c.put("sps", 2, b"\x03")
    c.put("pps", 3, b"\x04")

    assert [row["ps_id"] for row in c.entries("sps")] == [2, 7]
    assert [row["size"] for row in c.entries("sps")] == [1, 2]
    assert [row["ps_id"] for row in c.entries("pps")] == [3]
    assert c.entries("vps") == []


def test_invalidate_removes_entry_and_is_idempotent():
    c = cache()
    c.put("pps", 5, b"\x01")

    assert c.invalidate("pps", 5) is True
    with pytest.raises(ps.ParameterSetError) as gone:
        c.get("pps", 5)
    assert gone.value.code == "PS_ID_MISSING"

    assert c.invalidate("pps", 5) is False
    assert c.stats()["invalidated"] == 1


def test_snapshot_is_detached_and_restore_roundtrips():
    c = cache()
    c.put("sps", 1, b"\x01")
    c.put("sps", 1, b"\x02")
    c.activate("s", "sps", 1)

    state = c.snapshot()
    pristine = copy.deepcopy(state)
    c.put("sps", 1, b"\x03")
    assert c.get("sps", 1)["version"] == 3

    clone = cache()
    clone.restore(pristine)
    assert clone.get("sps", 1)["version"] == 2
    assert clone.get("sps", 1, version=1)["payload"] == b"\x01"
    assert clone.bind_frame("s")["active"]["sps"]["version"] == 2

    for bad in (None, {}, [], "state"):
        with pytest.raises(ps.ParameterSetError) as exc:
            clone.restore(bad)
        assert exc.value.code == "PS_SNAPSHOT_INVALID"
    assert clone.get("sps", 1)["version"] == 2


def test_concurrent_puts_keep_every_entry():
    c = cache(max_entries_per_kind=16)
    ids = list(range(8))
    errors = []
    barrier = threading.Barrier(len(ids))

    def worker(ps_id):
        try:
            barrier.wait(timeout=5)
            c.put("pps", ps_id, bytes([ps_id]))
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(ps_id,)) for ps_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [row["ps_id"] for row in c.entries("pps")] == ids
    assert c.stats()["puts"] == 8


def test_stats_track_each_operation():
    c = cache()
    c.put("sps", 1, b"\x01")
    c.put("sps", 1, b"\x01")
    c.activate("s", "sps", 1)
    c.put("sps", 2, b"\x02")
    c.activate("s", "sps", 2)
    c.invalidate("pps", 9)

    assert c.stats() == {"puts": 3, "changes": 2, "activations": 2,
                         "pending": 1, "invalidated": 0, "rejected": 0}
