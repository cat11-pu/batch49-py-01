import threading

import pytest

import bgp_origin_watch as bw

PREFIX = "203.0.113.0/24"
ORIGIN = 65001


def watcher(**kwargs):
    return bw.BgpOriginWatch(**kwargs)


def origin_watch(peer_list=("p1", "p2"), **kwargs):
    g = watcher(**kwargs)
    g.add_roa(PREFIX, ORIGIN)
    return g


def test_observe_validates_prefix_asn_path_and_peer():
    g = watcher()
    with pytest.raises(bw.BgpWatchError) as exc:
        g.observe("203.0.113.0/33", ORIGIN, [65010, ORIGIN], peer="p1")
    assert exc.value.code == "BGP_PREFIX_INVALID"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.observe(PREFIX, 0, [65010, ORIGIN], peer="p1")
    assert exc.value.code == "BGP_ASN_INVALID"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.observe(PREFIX, ORIGIN, [], peer="p1")
    assert exc.value.code == "BGP_PATH_INVALID"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.observe(PREFIX, ORIGIN, [65010, 65020, 65010], peer="p1")
    assert exc.value.code == "BGP_PATH_LOOP"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.observe(PREFIX, ORIGIN, [65010], peer="  ")
    assert exc.value.code == "BGP_PEER_INVALID"


def test_roa_registration_and_withdrawal():
    g = watcher()
    info = g.add_roa("198.51.100.0/24", ORIGIN)
    assert info["prefix"] == "198.51.100.0/24"
    assert info["max_length"] == 24
    with pytest.raises(bw.BgpWatchError) as exc:
        g.add_roa("198.51.100.0/24", ORIGIN, max_length=20)
    assert exc.value.code == "BGP_ROA_INVALID"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.add_roa("198.51.100.0/24", 0)
    assert exc.value.code == "BGP_ASN_INVALID"
    assert g.withdraw_roa("198.51.100.0/24", ORIGIN) is True
    assert g.withdraw_roa("198.51.100.0/24", ORIGIN) is False


def test_exact_roa_match_is_valid():
    g = origin_watch()
    out = g.observe(PREFIX, ORIGIN, [65010, ORIGIN], peer="p1", now=10)
    assert out["status"] == "valid"
    assert out["kind"] is None
    assert out["baseline"] == ORIGIN
    assert out["event_id"] is None
    assert g.events() == []
    assert g.stats()["valid"] == 1


def test_roa_max_length_allows_more_specific_origin_match():
    g = watcher()
    g.add_roa("198.51.100.0/16", ORIGIN, max_length=24)
    out = g.observe("198.51.100.0/24", ORIGIN, [65010, ORIGIN], peer="p1")
    assert out["status"] == "valid"
    out2 = g.observe("198.51.100.128/25", ORIGIN, [65010, ORIGIN], peer="p1")
    assert out2["status"] == "pending"
    assert out2["kind"] == "invalid_length"


def test_origin_mismatch_needs_distinct_peers_before_confirmation():
    g = origin_watch(confirm_peers=2)
    first = g.observe(PREFIX, 65002, [65020, 65002], peer="p1", now=10)
    assert first["status"] == "pending"
    assert first["kind"] == "roa_mismatch"
    assert first["peers"] == ["p1"]
    assert g.events() == []
    second = g.observe(PREFIX, 65002, [65020, 65002], peer="p2", now=11)
    assert second["status"] == "confirmed_hijack"
    assert second["event_id"] == "bgp-1"
    logged = g.events()
    assert len(logged) == 1
    assert logged[0]["prefix"] == PREFIX
    assert logged[0]["origin_as"] == 65002
    assert logged[0]["severity"] == "high"
    assert logged[0]["peers"] == ["p1", "p2"]


def test_too_specific_announcement_is_invalid_length():
    g = origin_watch(confirm_peers=2)
    g.add_roa(PREFIX, ORIGIN, max_length=24)
    g.observe("203.0.113.128/25", ORIGIN, [65010, ORIGIN], peer="p1")
    out = g.observe("203.0.113.128/25", ORIGIN, [65010, ORIGIN], peer="p2")
    assert out["status"] == "confirmed_hijack"
    assert out["kind"] == "invalid_length"
    assert g.events()[0]["severity"] == "medium"


def test_first_seen_then_origin_change_without_roa():
    g = watcher(confirm_peers=2)
    first = g.observe("192.0.2.0/24", 65010, [65010], peer="p1")
    assert first["status"] == "first_seen"
    assert first["baseline"] == 65010
    second = g.observe("192.0.2.0/24", 65020, [65030, 65020], peer="p2")
    assert second["status"] == "pending"
    assert second["kind"] == "origin_change"
    assert second["baseline"] == 65010
    assert g.events() == []


def test_repeated_peer_does_not_confirm_alone():
    g = origin_watch(confirm_peers=2)
    for now in (10, 11, 12):
        out = g.observe(PREFIX, 65002, [65020, 65002], peer="p1", now=now)
        assert out["status"] == "pending"
        assert out["peers"] == ["p1"]
    assert g.events() == []
    assert g.stats()["events"] == 0


def test_confirmed_event_is_not_duplicated_by_later_observation():
    g = origin_watch(confirm_peers=2)
    g.observe(PREFIX, 65002, [65020, 65002], peer="p1")
    g.observe(PREFIX, 65002, [65020, 65002], peer="p2")
    third = g.observe(PREFIX, 65002, [65030, 65020, 65002], peer="p3")
    assert third["status"] == "confirmed_hijack"
    assert third["event_id"] == "bgp-1"
    assert len(g.events()) == 1
    assert g.stats()["events"] == 1


def test_legitimate_origin_resolves_the_open_event():
    g = origin_watch(confirm_peers=2)
    g.observe(PREFIX, 65002, [65020, 65002], peer="p1")
    g.observe(PREFIX, 65002, [65020, 65002], peer="p2")
    resolved = g.observe(PREFIX, ORIGIN, [65010, ORIGIN], peer="p3", now=20)
    assert resolved["status"] == "valid"
    assert g.events() == []
    assert g.events(include_resolved=True)[0]["resolved"] is True
    assert g.stats()["resolved"] == 1


def test_more_specific_origin_change_is_critical():
    g = watcher(confirm_peers=2)
    g.add_roa("198.51.100.0/16", ORIGIN, max_length=24)
    g.observe("198.51.100.0/24", 65002, [65020, 65002], peer="p1")
    out = g.observe("198.51.100.0/24", 65002, [65020, 65002], peer="p2")
    assert out["kind"] == "more_specific"
    assert out["severity"] == "critical"
    assert g.events()[0]["kind"] == "more_specific"


def test_event_log_capacity_drops_oldest_entry():
    g = origin_watch(confirm_peers=1, max_events=1)
    g.add_roa("198.18.0.0/15", 65001, max_length=24)
    g.observe("198.18.0.0/24", 65002, [65020, 65002], peer="p1")
    assert len(g.events()) == 1
    g.observe("198.19.0.0/24", 65003, [65030, 65003], peer="p1")
    remaining = g.events()
    assert len(remaining) == 1
    assert remaining[0]["prefix"] == "198.19.0.0/24"
    assert g.stats()["events_dropped"] == 1


def test_ack_removes_event_from_default_view():
    g = origin_watch(confirm_peers=2)
    g.observe(PREFIX, 65002, [65020, 65002], peer="p1")
    g.observe(PREFIX, 65002, [65020, 65002], peer="p2")
    event_id = g.events()[0]["event_id"]
    assert g.ack(event_id) is True
    assert g.ack(event_id) is False
    assert g.events() == []
    assert g.events(include_resolved=True)[0]["acked"] is True
    assert g.stats()["acked"] == 1


def test_snapshot_and_restore_roundtrip():
    g = origin_watch(confirm_peers=2)
    g.observe(PREFIX, 65002, [65020, 65002], peer="p1")
    g.observe(PREFIX, 65002, [65020, 65002], peer="p2")
    snap = g.snapshot()
    assert snap["stats"]["events"] == 1
    g.observe("192.0.2.0/24", 65010, [65010], peer="p4")
    assert len(g.prefixes()) == 2
    g.restore(snap)
    assert len(g.prefixes()) == 1
    assert len(g.events()) == 1
    assert g.events()[0]["event_id"] == "bgp-1"
    with pytest.raises(bw.BgpWatchError) as exc:
        g.restore({"events": "nope"})
    assert exc.value.code == "BGP_SNAPSHOT_INVALID"
    assert len(g.events()) == 1


def test_concurrent_peers_confirm_exactly_one_event():
    g = origin_watch(confirm_peers=2)
    statuses = []
    lock = threading.Lock()

    def worker(index):
        try:
            out = g.observe(PREFIX, 65002, [65020, 65002], peer="p%d" % index, now=10 + index)
        except bw.BgpWatchError as exc:
            with lock:
                statuses.append(exc.code)
        else:
            with lock:
                statuses.append(out["status"])

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert set(statuses) <= {"pending", "confirmed_hijack"}
    assert "confirmed_hijack" in statuses
    assert len(g.events()) == 1
    assert g.stats()["events"] == 1
