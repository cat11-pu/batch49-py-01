import copy
import threading

import pytest

import match_avoid_registry as mr

NOW = 1000


def registry(**kwargs):
    return mr.MatchAvoidRegistry(**kwargs)


def ticket(ticket_id, player, enqueued_at):
    return mr.MatchTicket(ticket_id, player, enqueued_at)


def test_add_avoid_returns_edge_with_expiry():
    reg = registry(default_ttl=600)
    edge = reg.add_avoid("p1", "p2", now=NOW)
    assert isinstance(edge, mr.AvoidEdge)
    assert (edge.owner, edge.target) == ("p1", "p2")
    assert edge.created_at == NOW
    assert edge.expires_at == NOW + 600
    short = reg.add_avoid("p1", "p3", ttl=30, now=NOW)
    assert short.expires_at == NOW + 30
    assert reg.stats()["added"] == 2


def test_add_same_pair_refreshes_without_new_entry():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    again = reg.add_avoid("p1", "p2", ttl=120, now=NOW + 10)
    assert again.created_at == NOW + 10
    assert again.expires_at == NOW + 130
    assert len(reg.avoid_pairs("p1")) == 1
    assert reg.stats()["added"] == 1
    assert reg.stats()["refreshed"] == 1


def test_add_avoid_validates_arguments():
    reg = registry()
    bad_calls = [
        ("", "p2", {}),
        ("p1", "   ", {}),
        ("p1", "p1", {}),
        ("p1", "p2", {"ttl": 0}),
        ("p1", "p2", {"ttl": -5}),
        ("p1", 7, {}),
    ]
    for owner, target, extra in bad_calls:
        with pytest.raises(mr.MatchAvoidError) as exc:
            reg.add_avoid(owner, target, now=NOW, **extra)
        assert exc.value.code == "MATCH_AVOID_INVALID_REQUEST"
    assert reg.stats()["added"] == 0


def test_remove_avoid_toggles_and_counts():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    assert reg.remove_avoid("p1", "p2", now=NOW) is True
    assert reg.remove_avoid("p1", "p2", now=NOW) is False
    assert reg.stats()["removed"] == 1
    assert reg.avoid_pairs("p1") == []


def test_relax_level_maps_wait_to_tier():
    reg = registry(relax_thresholds=(60, 180, 300))
    waits = (0, 59, 60, 179, 180, 299, 300, 5000)
    assert [reg.relax_level(wait) for wait in waits] == [0, 0, 1, 1, 2, 2, 3, 3]


def test_relax_level_rejects_negative_wait():
    reg = registry()
    with pytest.raises(mr.MatchAvoidError) as exc:
        reg.relax_level(-1)
    assert exc.value.code == "MATCH_AVOID_INVALID_REQUEST"


def test_filter_drops_avoided_candidate_for_owner():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    main = ticket("t-main", "p1", NOW)
    candidates = [ticket("c-a", "p2", NOW + 5), ticket("c-b", "p3", NOW + 5)]
    kept = reg.filter_candidates(main, candidates, now=NOW + 30)
    assert [row.ticket_id for row in kept] == ["c-b"]
    assert reg.stats()["filtered"] == 1


def test_filter_keeps_unrelated_and_sorts_by_wait_then_id():
    reg = registry()
    main = ticket("t-main", "p1", NOW)
    candidates = [
        ticket("q1", "p2", NOW + 100),
        ticket("q0", "p3", NOW + 40),
        ticket("q2", "p4", NOW + 100),
    ]
    kept = reg.filter_candidates(main, candidates, now=NOW + 200)
    assert [row.ticket_id for row in kept] == ["q0", "q1", "q2"]


def test_filter_dedupes_ticket_ids_and_drops_self():
    reg = registry()
    main = ticket("t-main", "p1", NOW)
    candidates = [
        ticket("dup", "p2", NOW + 10),
        ticket("dup", "p2", NOW + 10),
        ticket("self", "p1", NOW + 10),
        ticket("other", "p4", NOW + 10),
    ]
    kept = reg.filter_candidates(main, candidates, now=NOW + 20)
    assert [row.ticket_id for row in kept] == ["dup", "other"]


def test_filter_after_expiry_ignores_stale_edge():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    main = ticket("t-main", "p1", NOW)
    candidates = [ticket("c-a", "p2", NOW)]
    kept = reg.filter_candidates(main, candidates, now=NOW + 600)
    assert [row.ticket_id for row in kept] == ["c-a"]
    assert reg.expire(now=NOW + 599) == 0
    assert reg.expire(now=NOW + 600) == 1
    assert reg.expire(now=NOW + 900) == 0


def test_long_wait_relaxes_single_edge():
    reg = registry(default_ttl=3600, relax_thresholds=(60, 180, 300))
    reg.add_avoid("p1", "p2", now=NOW)
    main = ticket("t-main", "p1", NOW)
    candidates = [ticket("c-a", "p2", NOW), ticket("c-b", "p3", NOW)]
    kept = reg.filter_candidates(main, candidates, now=NOW + 70)
    assert [row.ticket_id for row in kept] == ["c-a", "c-b"]
    assert reg.stats()["relaxed"] == 1


def test_expire_counts_only_due_edges():
    reg = registry()
    reg.add_avoid("p1", "p2", ttl=100, now=NOW)
    reg.add_avoid("p1", "p3", ttl=500, now=NOW)
    reg.add_avoid("p2", "p9", ttl=50, now=NOW)
    assert reg.expire(now=NOW + 49) == 0
    assert reg.expire(now=NOW + 100) == 2
    assert reg.expire(now=NOW + 499) == 0
    assert reg.expire(now=NOW + 500) == 1
    assert reg.stats()["expired"] == 3


def test_snapshot_is_deeply_detached():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    state = reg.snapshot()
    assert set(state) == {"schema", "edges", "stats"}
    state["edges"].clear()
    state["stats"]["added"] = 99
    assert reg.stats()["added"] == 1
    kept = reg.filter_candidates(ticket("t", "p1", NOW), [ticket("c", "p2", NOW)],
                                 now=NOW + 5)
    assert kept == []


def test_restore_roundtrip_and_rejects_malformed():
    reg = registry(default_ttl=600)
    reg.add_avoid("p1", "p2", now=NOW)
    state = reg.snapshot()
    clone = registry()
    clone.restore(copy.deepcopy(state))
    assert len(clone.avoid_pairs("p1")) == 1
    assert clone.stats()["added"] == 1

    bad_states = [
        None,
        {},
        {"schema": 2, "edges": [], "stats": {}},
        {"schema": 1, "edges": "nope", "stats": {}},
        {"schema": 1, "edges": [], "stats": {"added": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(mr.MatchAvoidError) as exc:
            clone.restore(bad)
        assert exc.value.code == "MATCH_AVOID_STATE_INVALID"
    assert len(clone.avoid_pairs("p1")) == 1


def test_concurrent_adds_keep_all_edges():
    reg = registry(default_ttl=600, max_avoid_per_player=64)
    errors = []
    barrier = threading.Barrier(16)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            reg.add_avoid("p9", f"x{index:02d}", now=NOW)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(reg.avoid_pairs("p9")) == 16
    assert reg.stats()["added"] == 16
