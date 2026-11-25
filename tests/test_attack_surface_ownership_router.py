import copy
import threading

import pytest

import attack_surface_ownership_router as ar

NOW = "2026-11-25T12:00:00+00:00"
LATER = "2026-11-25T13:00:00+00:00"


def router(**kwargs):
    return ar.AttackSurfaceOwnershipRouter(**kwargs)


def test_add_rule_records_selector_priority_and_specificity():
    rec = router()
    tag_rule = rec.add_rule("R-TAG", "team-a", priority=5, tag="payments", now=NOW)
    cidr_rule = rec.add_rule("R-CIDR", "team-b", cidr="10.0.0.0/8", now=NOW)
    domain_rule = rec.add_rule("R-DOMAIN", "team-c", domain_suffix="corp.example",
                               now=NOW)
    assert tag_rule.rule_id == "R-TAG"
    assert tag_rule.owner == "team-a"
    assert tag_rule.priority == 5
    assert tag_rule.tag == "payments"
    assert tag_rule.specificity == len("payments")
    assert cidr_rule.cidr == "10.0.0.0/8"
    assert cidr_rule.specificity == 8
    assert domain_rule.domain_suffix == "corp.example"
    assert domain_rule.specificity == len("corp.example")
    assert rec.stats()["rules"] == 3


def test_rule_validation_duplicate_capacity_and_removal():
    rec = router()
    bad_calls = [
        ("", "team-a", {"tag": "x"}),
        ("R-1", "", {"tag": "x"}),
        ("R-1", "team-a", {}),
        ("R-1", "team-a", {"tag": "x", "domain_suffix": "corp.example"}),
        ("R-1", "team-a", {"tag": "x", "priority": -1}),
        ("R-1", "team-a", {"cidr": "not-a-network"}),
    ]
    for rule_id, owner, kwargs in bad_calls:
        with pytest.raises(ar.RoutingError) as exc:
            rec.add_rule(rule_id, owner, now=NOW, **kwargs)
        assert exc.value.code == "ROUTER_INVALID_REQUEST"
    rec.add_rule("R-1", "team-a", tag="x", now=NOW)
    with pytest.raises(ar.RoutingError) as exc:
        rec.add_rule("R-1", "team-b", tag="y", now=NOW)
    assert exc.value.code == "ROUTER_DUPLICATE_RULE"

    capped = router(max_rules=2)
    capped.add_rule("R-1", "team-a", tag="a", now=NOW)
    capped.add_rule("R-2", "team-b", tag="b", now=NOW)
    with pytest.raises(ar.RoutingError) as exc:
        capped.add_rule("R-3", "team-c", tag="c", now=NOW)
    assert exc.value.code == "ROUTER_CAPACITY_EXCEEDED"
    assert capped.remove_rule("R-1") is True
    assert capped.remove_rule("missing") is False
    assert capped.stats()["rules"] == 1


def test_tag_rule_assigns_owner_and_records_history():
    rec = router()
    rule = rec.add_rule("R-TAG", "team-pay", priority=10, tag="payments", now=NOW)
    assignment = rec.register_asset(
        "a-1", tags=["payments", "prod"], domain="api.example.com",
        ip="10.1.2.3", now=NOW)
    assert assignment.asset_id == "a-1"
    assert assignment.owner == "team-pay"
    assert assignment.rule_id == rule.rule_id
    assert assignment.locked is False
    assert assignment.source == "rule"
    assert assignment.assigned_at == NOW
    assert rec.resolve("a-1") == assignment
    assert rec.history("a-1") == [assignment]
    assert rec.stats()["assets"] == 1
    assert rec.stats()["assigned"] == 1
    assert rec.stats()["unowned"] == 0


def test_priority_wins_over_specificity():
    rec = router()
    rec.add_rule("R-LOW", "team-specific", priority=1, tag="payment-gateway",
                 now=NOW)
    rec.add_rule("R-HIGH", "team-priority", priority=10, tag="payments", now=NOW)
    assignment = rec.register_asset(
        "a-1", tags=["payments", "payment-gateway"], now=NOW)
    assert assignment.owner == "team-priority"
    assert assignment.rule_id == "R-HIGH"


def test_unmatched_asset_enters_unowned_queue_and_can_be_claimed():
    rec = router()
    assignment = rec.register_asset("a-1", tags=["prod"], now=NOW)
    assert assignment.owner is None
    assert assignment.rule_id is None
    assert assignment.locked is False
    assert assignment.source == "unowned"
    rows = rec.unowned(NOW)
    assert [row.asset_id for row in rows] == ["a-1"]
    assert rows[0].since == NOW
    assert rows[0].last_seen == NOW
    claimed = rec.claim_unowned("a-1", "team-manual", now=LATER)
    assert claimed.owner == "team-manual"
    assert claimed.rule_id is None
    assert claimed.locked is True
    assert claimed.source == "manual"
    assert rec.unowned(LATER) == ()
    assert len(rec.history("a-1")) == 2
    assert rec.stats()["claimed"] == 1
    assert rec.stats()["assigned"] == 1
    with pytest.raises(ar.RoutingError) as exc:
        rec.claim_unowned("a-1", "team-other", now=LATER)
    assert exc.value.code == "ROUTER_INVALID_TRANSITION"


def test_domain_suffix_matches_only_on_label_boundary():
    rec = router()
    rec.add_rule("R-DOMAIN", "team-corp", domain_suffix="corp.example", now=NOW)
    rec.register_asset("a-1", domain="api.corp.example", now=NOW)
    rec.register_asset("a-2", domain="corp.example", now=NOW)
    rec.register_asset("a-3", domain="evilcorp.example", now=NOW)
    rec.register_asset("a-4", domain="api.corp.example.evil.com", now=NOW)
    assert rec.resolve("a-1").owner == "team-corp"
    assert rec.resolve("a-2").owner == "team-corp"
    assert rec.resolve("a-3").owner is None
    assert rec.resolve("a-4").owner is None


def test_cidr_rule_uses_network_containment():
    rec = router()
    rec.add_rule("R-CIDR", "team-net", cidr="10.0.0.0/8", now=NOW)
    rec.register_asset("a-1", ip="10.1.2.3", now=NOW)
    rec.register_asset("a-2", ip="11.1.2.3", now=NOW)
    rec.register_asset("a-3", ip=None, now=NOW)
    assert rec.resolve("a-1").owner == "team-net"
    assert rec.resolve("a-1").rule_id == "R-CIDR"
    assert rec.resolve("a-2").owner is None
    assert rec.resolve("a-3").owner is None


def test_release_lock_recomputes_and_returns_to_unowned():
    rec = router()
    rec.register_asset("a-1", now=NOW)
    rec.claim_unowned("a-1", "team-manual", now=NOW)
    released = rec.release_lock("a-1", now=LATER)
    assert released.owner is None
    assert released.locked is False
    assert released.source == "unowned"
    assert [row.asset_id for row in rec.unowned(LATER)] == ["a-1"]
    assert len(rec.history("a-1")) == 3
    with pytest.raises(ar.RoutingError) as exc:
        rec.release_lock("a-1", now=LATER)
    assert exc.value.code == "ROUTER_INVALID_TRANSITION"


def test_recompute_applies_existing_rule_changes_to_unlocked_assets():
    rec = router()
    rec.add_rule("R-LOW", "team-a", priority=1, tag="prod", now=NOW)
    rec.register_asset("a-1", tags=["prod"], now=NOW)
    rec.add_rule("R-HIGH", "team-b", priority=10, tag="prod", now=NOW)
    assert rec.resolve("a-1").owner == "team-a"
    assert rec.recompute(LATER) == ()
    updated = rec.resolve("a-1")
    assert updated.owner == "team-b"
    assert updated.rule_id == "R-HIGH"
    assert updated.locked is False
    assert len(rec.history("a-1")) == 2
    assert rec.stats()["recomputed"] == 1


def test_recompute_after_rule_removal_moves_asset_to_unowned():
    rec = router()
    rec.add_rule("R-1", "team-a", tag="prod", now=NOW)
    rec.register_asset("a-1", tags=["prod"], now=NOW)
    assert rec.remove_rule("R-1") is True
    assert rec.resolve("a-1").owner == "team-a"
    assert rec.recompute(LATER) == ()
    assert rec.resolve("a-1").owner is None
    assert rec.resolve("a-1").source == "unowned"
    assert [row.asset_id for row in rec.unowned(LATER)] == ["a-1"]
    assert len(rec.history("a-1")) == 2
    assert rec.stats()["recomputed"] == 1
    assert rec.stats()["unowned"] == 1


def test_invalid_asset_inputs_and_unknown_identifiers():
    rec = router(max_assets=1)
    with pytest.raises(ar.RoutingError) as exc:
        rec.register_asset("", now=NOW)
    assert exc.value.code == "ROUTER_INVALID_REQUEST"
    with pytest.raises(ar.RoutingError) as exc:
        rec.register_asset("a-1", ip="bad-ip", now=NOW)
    assert exc.value.code == "ROUTER_INVALID_REQUEST"
    rec.register_asset("a-1", now=NOW)
    with pytest.raises(ar.RoutingError) as exc:
        rec.register_asset("a-1", now=NOW)
    assert exc.value.code == "ROUTER_DUPLICATE_ASSET"
    with pytest.raises(ar.RoutingError) as exc:
        rec.register_asset("a-2", now=NOW)
    assert exc.value.code == "ROUTER_CAPACITY_EXCEEDED"
    for call in (
        lambda: rec.resolve("missing"),
        lambda: rec.update_asset("missing", tags=["x"], now=NOW),
        lambda: rec.claim_unowned("missing", "team-x", now=NOW),
        lambda: rec.release_lock("missing", now=NOW),
        lambda: rec.history("missing"),
    ):
        with pytest.raises(ar.RoutingError) as exc:
            call()
        assert exc.value.code == "ROUTER_UNKNOWN_ASSET"
    assert rec.stats()["assets"] == 1


def test_stats_track_current_counts_and_operations():
    rec = router()
    rec.add_rule("R-1", "team-prod", tag="prod", now=NOW)
    rec.register_asset("a-1", tags=["prod"], now=NOW)
    rec.register_asset("a-2", now=NOW)
    rec.claim_unowned("a-2", "team-manual", now=NOW)
    rec.add_rule("R-2", "team-pay", tag="payments", now=NOW)
    rec.register_asset("a-3", tags=["payments"], now=NOW)
    assert rec.stats() == {
        "assets": 3, "rules": 2, "assigned": 3, "unowned": 0,
        "claimed": 1, "recomputed": 0, "conflicts": 0,
    }
    assert rec.conflicts() == ()


def test_snapshot_is_detached_and_restore_roundtrips():
    rec = router()
    rec.add_rule("R-1", "team-prod", tag="prod", now=NOW)
    rec.register_asset("a-1", tags=["prod"], now=NOW)
    rec.register_asset("a-2", now=NOW)
    rec.claim_unowned("a-2", "team-manual", now=NOW)
    state = rec.snapshot()
    assert set(state) == {"version", "assets", "rules", "assignments", "unowned",
                          "history", "conflicts", "counters"}
    pristine = copy.deepcopy(state)
    state["assignments"].clear()
    state["counters"]["assets"] = 999
    assert rec.resolve("a-1").owner == "team-prod"
    assert rec.stats()["assets"] == 2

    clone = router()
    clone.restore(pristine)
    assert clone.resolve("a-1") == rec.resolve("a-1")
    assert clone.resolve("a-2") == rec.resolve("a-2")
    assert clone.history("a-2") == rec.history("a-2")
    assert clone.unowned(LATER) == rec.unowned(LATER)
    assert clone.conflicts() == rec.conflicts()
    assert clone.stats() == rec.stats()


def test_restore_rejects_malformed_state_without_touching_live_data():
    rec = router()
    rec.register_asset("a-1", now=NOW)
    good = rec.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "assets": good["assets"], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": [], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": {},
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": good["rules"],
         "assignments": [], "unowned": good["unowned"],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": [],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": [], "conflicts": good["conflicts"],
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": good["history"], "conflicts": {},
         "counters": good["counters"]},
        {"version": 1, "assets": good["assets"], "rules": good["rules"],
         "assignments": good["assignments"], "unowned": good["unowned"],
         "history": good["history"], "conflicts": good["conflicts"],
         "counters": {"assets": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(ar.RoutingError) as exc:
            rec.restore(bad)
        assert exc.value.code == "ROUTER_SNAPSHOT_INVALID"
    assert rec.resolve("a-1").asset_id == "a-1"
    assert rec.stats()["assets"] == 1
    assert rec.stats()["unowned"] == 1


def test_concurrent_registration_assigns_each_asset_once():
    rec = router()
    rec.add_rule("R-1", "team-prod", tag="prod", now=NOW)
    errors = []
    barrier = threading.Barrier(20)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            rec.register_asset(f"a-{index:02d}", tags=["prod"], now=NOW)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert rec.stats()["assets"] == 20
    assert rec.stats()["assigned"] == 20
    assert rec.stats()["unowned"] == 0
    for index in range(20):
        assignment = rec.resolve(f"a-{index:02d}")
        assert assignment.owner == "team-prod"
        assert assignment.rule_id == "R-1"
        assert len(rec.history(f"a-{index:02d}")) == 1
