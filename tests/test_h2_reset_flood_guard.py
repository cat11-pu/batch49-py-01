import threading

import pytest

import h2_reset_flood_guard as h2


def guard(**kwargs):
    return h2.H2ResetFloodGuard(**kwargs)


def armed(g, conn_id="c1", ids=(1, 3, 5), now=10):
    g.open_connection(conn_id, now=now - 1)
    for sid in ids:
        g.open_stream(conn_id, sid, now=now)


def test_connection_and_stream_accounting():
    g = guard()
    info = g.open_connection("c1", now=5)
    assert info["conn_id"] == "c1"
    assert info["state"] == "open"
    first = g.open_stream("c1", 1, now=6)
    assert first["stream_id"] == 1
    assert first["state"] == "open"
    g.open_stream("c1", 3, now=7)
    snap = g.state("c1", now=8)
    assert snap["concurrent"] == 2
    assert snap["last_stream_id"] == 3
    assert g.stats()["streams_opened"] == 2


def test_connection_identity_and_capacity():
    g = guard(max_connections=2)
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_connection("   ")
    assert exc.value.code == "H2_CONN_INVALID"
    g.open_connection("c1")
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_connection("c1")
    assert exc.value.code == "H2_CONN_EXISTS"
    g.open_connection("c2")
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_connection("c3")
    assert exc.value.code == "H2_CONNECTION_LIMIT"
    assert g.stats()["connections_opened"] == 2


def test_stream_ids_must_be_odd_and_strictly_increasing():
    g = guard()
    g.open_connection("c1")
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("c1", 2)
    assert exc.value.code == "H2_STREAM_ID_INVALID"
    g.open_stream("c1", 5)
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("c1", 3)
    assert exc.value.code == "H2_STREAM_ID_INVALID"
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("nope", 1)
    assert exc.value.code == "H2_CONN_UNKNOWN"


def test_concurrency_limit_refuses_extra_streams():
    g = guard(max_concurrent_streams=2)
    g.open_connection("c1")
    g.open_stream("c1", 1)
    g.open_stream("c1", 3)
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("c1", 5)
    assert exc.value.code == "H2_CONCURRENCY_EXCEEDED"
    assert g.close_stream("c1", 1) is True
    out = g.open_stream("c1", 5)
    assert out["stream_id"] == 5
    assert g.state("c1")["concurrent"] == 2


def test_closing_streams_is_idempotent():
    g = guard()
    g.open_connection("c1")
    g.open_stream("c1", 1)
    assert g.close_stream("c1", 1) is True
    assert g.close_stream("c1", 1) is False
    assert g.close_stream("c1", 99) is False
    assert g.state("c1")["concurrent"] == 0


def test_reset_closes_stream_and_counts_even_after_close():
    g = guard()
    armed(g, ids=(1,))
    first = g.record_reset("c1", 1, now=11)
    assert first["reset_count"] == 1
    assert first["stream_state"] == "closed"
    assert g.state("c1", now=11)["concurrent"] == 0
    second = g.record_reset("c1", 1, now=12)
    assert second["reset_count"] == 2
    assert g.stats()["resets"] == 2


def test_reset_on_never_opened_stream_is_refused():
    g = guard()
    armed(g, ids=(1,))
    with pytest.raises(h2.H2GuardError) as exc:
        g.record_reset("c1", 9, now=11)
    assert exc.value.code == "H2_STREAM_UNKNOWN"
    assert g.stats()["resets"] == 0
    assert g.state("c1", now=11)["concurrent"] == 1


def test_reset_window_expires_old_events():
    g = guard(window_seconds=10, reset_limit=99, min_samples=99)
    armed(g)
    g.record_reset("c1", 1, now=1)
    g.record_reset("c1", 3, now=2)
    g.record_reset("c1", 5, now=3)
    verdict = g.evaluate("c1", now=12)
    assert verdict["reset_count"] == 2
    assert verdict["level"] == "normal"
    assert verdict["action"] == "NONE"


def test_storm_threshold_triggers_goaway_and_quarantine():
    g = guard(reset_limit=3, min_samples=3, quarantine_seconds=20)
    armed(g)
    for offset, sid in enumerate((1, 3, 5)):
        g.record_reset("c1", sid, now=10 + offset)
    verdict = g.evaluate("c1", now=13)
    assert verdict["reset_count"] == 3
    assert verdict["level"] == "storm"
    assert verdict["action"] == "GOAWAY"
    assert verdict["quarantined"] is True
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("c1", 7, now=14)
    assert exc.value.code == "H2_QUARANTINED"
    assert g.stats()["quarantines"] == 1


def test_quarantine_state_expires_after_the_window():
    g = guard(reset_limit=3, min_samples=3, quarantine_seconds=10)
    armed(g)
    for offset, sid in enumerate((1, 3, 5)):
        g.record_reset("c1", sid, now=100 + offset)
    g.evaluate("c1", now=103)
    assert g.state("c1", now=105)["quarantined"] is True
    assert g.state("c1", now=114)["quarantined"] is False
    out = g.open_stream("c1", 7, now=114)
    assert out["stream_id"] == 7


def test_manual_quarantine_and_release():
    g = guard(quarantine_seconds=30)
    g.open_connection("c1", now=10)
    order = g.quarantine("c1", now=20)
    assert order["action"] == "GOAWAY"
    assert order["until"] == 50
    with pytest.raises(h2.H2GuardError) as exc:
        g.open_stream("c1", 1, now=21)
    assert exc.value.code == "H2_QUARANTINED"
    assert g.release("c1", now=21) is True
    assert g.release("c1", now=22) is False
    out = g.open_stream("c1", 1, now=22)
    assert out["stream_id"] == 1


def test_ratio_without_storm_is_flagged_as_suspicious():
    g = guard(window_seconds=60, reset_limit=20, ratio_limit=0.5, min_samples=4)
    armed(g, ids=(1, 3, 5, 7))
    g.record_reset("c1", 1, now=10)
    g.record_reset("c1", 3, now=11)
    verdict = g.evaluate("c1", now=12)
    assert verdict["opened_count"] == 4
    assert verdict["reset_count"] == 2
    assert verdict["ratio"] == 0.5
    assert verdict["level"] == "suspicious"
    assert verdict["action"] == "MONITOR"


def test_snapshot_restore_roundtrip_is_detached():
    g = guard()
    armed(g, ids=(1, 3))
    g.record_reset("c1", 1, now=12)
    snap = g.snapshot()
    assert snap["stats"]["streams_opened"] == 2
    g.open_stream("c1", 5, now=13)
    g.record_reset("c1", 5, now=14)
    g.restore(snap)
    view = g.state("c1", now=15)
    assert view["concurrent"] == 1
    assert view["last_stream_id"] == 3
    assert view["reset_count"] == 1
    assert snap["stats"]["streams_opened"] == 2
    assert snap["stats"]["resets"] == 1


def test_restore_rejects_malformed_state_without_touching_the_guard():
    g = guard()
    armed(g, ids=(1,))
    with pytest.raises(h2.H2GuardError) as exc:
        g.restore({"connections": []})
    assert exc.value.code == "H2_SNAPSHOT_INVALID"
    assert g.state("c1", now=20)["concurrent"] == 1
    assert g.stats()["streams_opened"] == 1


def test_concurrent_evaluation_quarantines_exactly_once():
    g = guard(reset_limit=3, min_samples=3, quarantine_seconds=30)
    armed(g)
    for offset, sid in enumerate((1, 3, 5)):
        g.record_reset("c1", sid, now=10 + offset)
    actions = []
    lock = threading.Lock()

    def worker():
        try:
            verdict = g.evaluate("c1", now=13)
        except h2.H2GuardError as exc:
            with lock:
                actions.append(exc.code)
        else:
            with lock:
                actions.append(verdict["action"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(actions) == ["GOAWAY"] + ["QUARANTINED"] * 7
    assert g.stats()["quarantines"] == 1
