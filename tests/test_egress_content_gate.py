import copy
import threading

import pytest

import egress_content_gate as eg

STRICT_TYPES = ("text/plain",)


def gate(**kwargs):
    return eg.EgressContentGate(**kwargs)


def pdf_gate():
    g = gate()
    g.set_target_policy("cdn", allowed_types=("application/pdf",), max_bytes=2000)
    return g


def zip_gate(**kwargs):
    g = gate()
    g.set_target_policy("cdn", allowed_types=("application/zip",),
                        max_bytes=100000, archive_depth=2,
                        archive_uncompressed_bytes=50000, **kwargs)
    return g


def test_set_target_policy_normalizes_and_versions():
    g = gate()
    policy = g.set_target_policy(
        " CDN ", allowed_types=("image/png", "application/pdf", "image/png"),
        max_bytes=2048)
    assert policy == {"target": "cdn",
                      "allowed_types": ("application/pdf", "image/png"),
                      "max_bytes": 2048, "archive_depth": 2,
                      "archive_uncompressed_bytes": 104857600, "version": 1}
    assert g.target_policy("cdn") == policy
    assert g.target_policy("CDN") == policy


def test_policy_update_bumps_version_and_invalid_policy_is_rejected():
    g = gate()
    g.set_target_policy("cdn", allowed_types=("text/plain",), max_bytes=100,
                        archive_depth=0, archive_uncompressed_bytes=0)
    updated = g.set_target_policy("cdn", allowed_types=("image/gif",),
                                  max_bytes=400)
    assert updated["version"] == 2
    assert updated["allowed_types"] == ("image/gif",)
    assert updated["max_bytes"] == 400

    with pytest.raises(eg.EgressError) as exc:
        g.set_target_policy("  ", allowed_types=("text/plain",), max_bytes=10)
    assert exc.value.code == "GATE_TARGET_INVALID"

    bad_calls = [
        dict(allowed_types=(), max_bytes=10),
        dict(allowed_types=("text/plain", 5), max_bytes=10),
        dict(allowed_types=("text/plain",), max_bytes=0),
        dict(allowed_types=("text/plain",), max_bytes=10, archive_depth=-1),
        dict(allowed_types=("text/plain",), max_bytes=10,
             archive_uncompressed_bytes=-1),
    ]
    for kwargs in bad_calls:
        with pytest.raises(eg.EgressError) as exc:
            g.set_target_policy("other", **kwargs)
        assert exc.value.code == "GATE_POLICY_INVALID"

    assert g.target_policy("cdn")["version"] == 2
    assert g.target_policy("cdn")["allowed_types"] == ("image/gif",)


def test_target_limit_and_unknown_target_strict_policy():
    g = gate(max_targets=1)
    g.set_target_policy("a", allowed_types=("text/plain",), max_bytes=10)
    with pytest.raises(eg.EgressError) as exc:
        g.set_target_policy("b", allowed_types=("text/plain",), max_bytes=10)
    assert exc.value.code == "GATE_TARGET_LIMIT"

    strict = g.target_policy("unknown")
    assert strict == {"target": "unknown", "allowed_types": STRICT_TYPES,
                      "max_bytes": 1048576, "archive_depth": 0,
                      "archive_uncompressed_bytes": 0, "version": 0}

    allowed = g.inspect("mystery", filename="note.txt", content_type="text/plain",
                        magic=b"hello world", size_bytes=1000)
    assert allowed.decision == "allow"
    assert allowed.policy_version == 0

    denied = g.inspect("mystery", filename="blob.bin",
                       content_type="application/octet-stream",
                       magic=b"\x00\x01\x02", size_bytes=1000)
    assert denied.decision == "deny"
    assert denied.reasons == ("type_not_allowed",)


def test_inspect_allows_matching_type_within_size():
    g = pdf_gate()
    out = g.inspect("cdn", filename="report.pdf", content_type="application/pdf",
                    magic=b"%PDF-1.7", size_bytes=1500)
    assert out == eg.Inspection("cdn", "allow", "application/pdf",
                                "application/pdf", 1500, (), 1)


def test_inspect_denies_type_outside_policy():
    g = pdf_gate()
    out = g.inspect("cdn", filename="photo.png", content_type="image/png",
                    magic=b"\x89PNG\r\n\x1a\n", size_bytes=1000)
    assert out.decision == "deny"
    assert out.sniffed_type == "image/png"
    assert out.reasons == ("type_not_allowed",)
    assert out.policy_version == 1


def test_inspect_denies_oversize_payload():
    g = pdf_gate()
    out = g.inspect("cdn", filename="big.pdf", content_type="application/pdf",
                    magic=b"%PDF-1.7", size_bytes=2500)
    assert out.decision == "deny"
    assert out.reasons == ("size_exceeded",)
    assert out.size_bytes == 2500


def test_inspect_accepts_archive_within_depth_and_total():
    g = zip_gate()
    out = g.inspect("cdn", filename="bundle.zip", content_type="application/zip",
                    magic=b"PK\x03\x04", size_bytes=4000,
                    archive_entries=[("a.txt", 1000), ("docs/b.txt", 2000)])
    assert out.decision == "allow"
    assert out.sniffed_type == "application/zip"
    assert out.reasons == ()


def test_inspect_denies_archive_over_uncompressed_budget():
    g = zip_gate()
    out = g.inspect("cdn", filename="bomb.zip", content_type="application/zip",
                    magic=b"PK\x03\x04", size_bytes=4000,
                    archive_entries=[("a.txt", 60000)])
    assert out.decision == "deny"
    assert out.reasons == ("archive_size_exceeded",)


def test_inspect_rejects_malformed_request():
    g = pdf_gate()
    cases = [
        dict(filename="", content_type="application/pdf", magic=b"%PDF-",
             size_bytes=10),
        dict(filename="a.pdf", content_type="   ", magic=b"%PDF-",
             size_bytes=10),
        dict(filename="a.pdf", content_type="application/pdf", magic=b"%PDF-",
             size_bytes=-1),
        dict(filename="a.pdf", content_type="application/pdf", magic="not-bytes",
             size_bytes=10),
    ]
    for kwargs in cases:
        with pytest.raises(eg.EgressError) as exc:
            g.inspect("cdn", **kwargs)
        assert exc.value.code == "GATE_REQUEST_INVALID"


def test_reserve_holds_bytes_before_transfer():
    g = gate()
    quota = g.set_tenant_quota(" t1 ", window_bytes=1000, window_ms=1000)
    assert quota == {"tenant": "t1", "window_bytes": 1000, "window_ms": 1000}

    reservation = g.reserve("t1", " CDN ", size_bytes=400, now_ms=5000)
    assert reservation == eg.Reservation("rsv-1", "t1", "cdn", 400, 5000, 6000)
    assert g.usage("t1") == {"tenant": "t1", "window_start_ms": 5000,
                             "window_ms": 1000, "used_bytes": 0,
                             "pending_bytes": 400, "reservations": 1}
    assert g.stats()["reserved"] == 1


def test_reserve_rejects_over_quota_and_reservation_cap():
    g = gate()
    g.set_tenant_quota("t1", window_bytes=100, window_ms=1000)
    g.reserve("t1", "cdn", size_bytes=60, now_ms=1000)
    with pytest.raises(eg.EgressError) as exc:
        g.reserve("t1", "cdn", size_bytes=50, now_ms=1000)
    assert exc.value.code == "GATE_QUOTA_EXCEEDED"
    assert g.usage("t1")["pending_bytes"] == 60
    assert g.usage("t1")["reservations"] == 1

    capped = gate(max_reservations_per_tenant=2)
    capped.set_tenant_quota("t1", window_bytes=1000)
    capped.reserve("t1", "cdn", size_bytes=1, now_ms=1000)
    capped.reserve("t1", "cdn", size_bytes=1, now_ms=1000)
    with pytest.raises(eg.EgressError) as exc:
        capped.reserve("t1", "cdn", size_bytes=1, now_ms=1000)
    assert exc.value.code == "GATE_RESERVATION_LIMIT"


def test_concurrent_reservations_cannot_exceed_quota():
    g = gate()
    g.set_tenant_quota("t1", window_bytes=300, window_ms=60000)
    barrier = threading.Barrier(10)
    outcomes = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            g.reserve("t1", "cdn", size_bytes=100, now_ms=1000)
            outcomes.append("reserved")
        except eg.EgressError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert outcomes.count("reserved") == 3
    assert outcomes.count("GATE_QUOTA_EXCEEDED") == 7
    usage = g.usage("t1")
    assert usage["pending_bytes"] == 300
    assert usage["reservations"] == 3


def test_commit_moves_pending_bytes_to_used():
    g = gate()
    g.set_tenant_quota("t1", window_bytes=1000, window_ms=1000)
    reservation = g.reserve("t1", "cdn", size_bytes=300, now_ms=1000)

    out = g.commit("t1", reservation.reservation_id, now_ms=1500)
    assert out == {"reservation_id": "rsv-1", "tenant": "t1",
                   "committed_bytes": 300, "used_bytes": 300,
                   "pending_bytes": 0}
    assert g.usage("t1")["used_bytes"] == 300
    assert g.usage("t1")["pending_bytes"] == 0

    with pytest.raises(eg.EgressError) as exc:
        g.commit("t1", reservation.reservation_id, now_ms=1600)
    assert exc.value.code == "GATE_RESERVATION_UNKNOWN"
    with pytest.raises(eg.EgressError) as exc:
        g.commit("t1", "rsv-404", now_ms=1600)
    assert exc.value.code == "GATE_RESERVATION_UNKNOWN"


def test_release_is_idempotent_and_blocks_late_commit():
    g = gate()
    g.set_tenant_quota("t1", window_bytes=1000, window_ms=1000)
    reservation = g.reserve("t1", "cdn", size_bytes=200, now_ms=1000)

    assert g.release("t1", reservation.reservation_id, now_ms=1200) is True
    assert g.release("t1", reservation.reservation_id, now_ms=1300) is False
    assert g.release("t1", "rsv-404", now_ms=1300) is False
    with pytest.raises(eg.EgressError) as exc:
        g.commit("t1", reservation.reservation_id, now_ms=1400)
    assert exc.value.code == "GATE_RESERVATION_UNKNOWN"


def test_window_rollover_stats_and_snapshot_roundtrip():
    g = gate()
    g.set_tenant_quota("t1", window_bytes=100, window_ms=1000)
    first = g.reserve("t1", "cdn", size_bytes=40, now_ms=1000)
    g.commit("t1", first.reservation_id, now_ms=1100)
    assert g.usage("t1")["used_bytes"] == 40

    g.reserve("t1", "cdn", size_bytes=80, now_ms=2000)
    usage = g.usage("t1")
    assert (usage["window_start_ms"], usage["used_bytes"],
            usage["pending_bytes"], usage["reservations"]) == (2000, 0, 80, 1)

    with pytest.raises(eg.EgressError) as exc:
        g.reserve("t1", "cdn", size_bytes=30, now_ms=2000)
    assert exc.value.code == "GATE_QUOTA_EXCEEDED"
    with pytest.raises(eg.EgressError) as exc:
        g.commit("t1", first.reservation_id, now_ms=2000)
    assert exc.value.code == "GATE_RESERVATION_UNKNOWN"

    assert g.stats() == {"policies": 0, "inspections": 0, "allowed": 0,
                         "denied": 0, "reserved": 2, "committed": 1,
                         "released": 0, "quota_rejections": 1}

    state = g.snapshot()
    assert set(state) == {"version", "policies", "quotas", "windows",
                          "counters"}
    pristine = copy.deepcopy(state)
    state["windows"].clear()
    state["counters"]["reserved"] = 99
    assert g.usage("t1")["pending_bytes"] == 80
    assert g.stats()["reserved"] == 2

    clone = gate()
    clone.restore(pristine)
    assert clone.usage("t1") == g.usage("t1")
    assert clone.stats() == g.stats()

    bad_states = [
        None,
        {},
        {"version": 2, "policies": {}, "quotas": {}, "windows": {},
         "counters": {}},
        {"version": 1, "policies": {}, "quotas": {}, "windows": {}},
        {"version": 1, "policies": [], "quotas": {}, "windows": {},
         "counters": {}},
        {"version": 1, "policies": {}, "quotas": {}, "windows": {},
         "counters": []},
    ]
    for bad in bad_states:
        with pytest.raises(eg.EgressError) as exc:
            clone.restore(bad)
        assert exc.value.code == "GATE_SNAPSHOT_INVALID"
    assert clone.usage("t1")["pending_bytes"] == 80
