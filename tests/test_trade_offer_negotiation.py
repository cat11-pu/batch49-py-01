import copy

import pytest

import trade_offer_negotiation as tn

NOW = "2026-11-25T09:00:00+00:00"
LATER = "2026-11-25T09:01:00+00:00"
TOO_LATE = "2026-11-25T09:01:01+00:00"


def negotiation(ttl=300, max_versions=20):
    neg = tn.TradeOfferNegotiation(offer_ttl=ttl, max_versions=max_versions)
    neg.open_session(session_id="s1", player_a="alice", player_b="bob",
                     now=NOW)
    neg.set_inventory("alice", {"sword": 1, "gem": 3})
    neg.set_inventory("bob", {"gold": 10})
    return neg


def open_offer(neg, proposer="alice", give=None, want=None, now=NOW):
    if give is None:
        give = {"sword": 1}
    if want is None:
        want = {"gold": 5}
    return neg.create_offer(session_id="s1", proposer=proposer, give=give,
                            want=want, now=now)


def test_open_session_validates_and_rejects_duplicates():
    neg = tn.TradeOfferNegotiation()
    for kwargs in [
        dict(session_id="", player_a="alice", player_b="bob", now=NOW),
        dict(session_id="s1", player_a="alice", player_b="alice", now=NOW),
        dict(session_id="s1", player_a="", player_b="bob", now=NOW),
    ]:
        with pytest.raises(tn.TradeError) as exc:
            neg.open_session(**kwargs)
        assert exc.value.code == "TRADE_INVALID_SESSION"

    neg.open_session(session_id="s1", player_a="alice", player_b="bob",
                     now=NOW)
    with pytest.raises(tn.TradeError) as exc:
        neg.open_session(session_id="s1", player_a="alice", player_b="bob",
                         now=NOW)
    assert exc.value.code == "TRADE_SESSION_EXISTS"


def test_create_offer_normalizes_items_and_sets_expiry():
    neg = negotiation()
    offer = open_offer(neg)
    assert offer.version == 1
    assert offer.proposer == "alice"
    assert offer.give == (("sword", 1),)
    assert offer.want == (("gold", 5),)
    assert offer.created_at == NOW
    assert offer.expires_at == "2026-11-25T09:05:00+00:00"
    assert neg.versions_of("s1") == [offer]
    assert neg.stats()["offers"] == 1


def test_create_offer_twice_is_rejected():
    neg = negotiation()
    open_offer(neg)
    with pytest.raises(tn.TradeError) as exc:
        open_offer(neg, give={"gem": 1}, want={"gold": 2})
    assert exc.value.code == "TRADE_OFFER_EXISTS"
    assert len(neg.versions_of("s1")) == 1


def test_counter_offer_bumps_version_and_alternates_sides():
    neg = negotiation()
    open_offer(neg)
    second = neg.counter_offer(session_id="s1", proposer="bob",
                               give={"gold": 4}, want={"sword": 1}, now=LATER)
    third = neg.counter_offer(session_id="s1", proposer="alice",
                              give={"sword": 1, "gem": 1}, want={"gold": 6},
                              now=LATER)
    assert second.version == 2
    assert second.proposer == "bob"
    assert third.version == 3
    assert third.proposer == "alice"
    history = neg.versions_of("s1")
    assert [(offer.version, offer.proposer) for offer in history] == [
        (1, "alice"), (2, "bob"), (3, "alice")]
    assert neg.stats()["counters"] == 2


def test_both_sides_accept_and_inventories_swap_atomically():
    neg = negotiation()
    open_offer(neg)
    first = neg.accept(session_id="s1", player="alice", version=1, now=LATER)
    assert first.status == "pending"
    assert first.version == 1
    assert neg.state(session_id="s1", now=LATER) == "open"
    assert neg.inventory_of("alice") == {"sword": 1, "gem": 3}

    second = neg.accept(session_id="s1", player="bob", version=1, now=LATER)
    assert second.status == "completed"
    assert neg.state(session_id="s1", now=LATER) == "accepted"
    assert neg.inventory_of("alice") == {"gem": 3, "gold": 5}
    assert neg.inventory_of("bob") == {"gold": 5, "sword": 1}


def test_duplicate_accept_by_same_side_is_idempotent():
    neg = negotiation()
    open_offer(neg)
    assert neg.accept(session_id="s1", player="alice", version=1,
                      now=LATER).status == "pending"
    assert neg.accept(session_id="s1", player="alice", version=1,
                      now=LATER).status == "pending"
    assert neg.stats()["pending_accepts"] == 1
    assert neg.accept(session_id="s1", player="bob", version=1,
                      now=LATER).status == "completed"
    assert neg.inventory_of("bob") == {"gold": 5, "sword": 1}
    assert neg.stats()["accepted"] == 1


def test_outsider_and_unknown_session_are_rejected():
    neg = negotiation()
    open_offer(neg)
    with pytest.raises(tn.TradeError) as exc:
        neg.accept(session_id="s1", player="charlie", version=1, now=LATER)
    assert exc.value.code == "TRADE_NOT_PARTY"
    with pytest.raises(tn.TradeError) as exc:
        neg.state(session_id="missing", now=LATER)
    assert exc.value.code == "TRADE_UNKNOWN_SESSION"


def test_reject_closes_session_and_blocks_accept():
    neg = negotiation()
    open_offer(neg)
    neg.reject(session_id="s1", player="bob", version=1, now=LATER)
    assert neg.state(session_id="s1", now=LATER) == "rejected"
    with pytest.raises(tn.TradeError) as exc:
        neg.accept(session_id="s1", player="alice", version=1, now=LATER)
    assert exc.value.code == "TRADE_SESSION_CLOSED"


def test_cancel_closes_session_and_blocks_reject():
    neg = negotiation()
    open_offer(neg)
    neg.cancel(session_id="s1", player="alice", now=LATER)
    assert neg.state(session_id="s1", now=LATER) == "cancelled"
    with pytest.raises(tn.TradeError) as exc:
        neg.reject(session_id="s1", player="bob", version=1, now=LATER)
    assert exc.value.code == "TRADE_SESSION_CLOSED"
    assert neg.stats()["cancelled"] == 1


def test_offer_expires_after_ttl():
    neg = negotiation(ttl=60)
    open_offer(neg)
    with pytest.raises(tn.TradeError) as exc:
        neg.accept(session_id="s1", player="bob", version=1, now=TOO_LATE)
    assert exc.value.code == "TRADE_OFFER_EXPIRED"
    assert neg.state(session_id="s1", now=TOO_LATE) == "expired"
    assert neg.stats()["expired"] == 1


def test_version_limit_stops_further_counters():
    neg = negotiation(max_versions=3)
    open_offer(neg)
    neg.counter_offer(session_id="s1", proposer="bob", give={"gold": 4},
                      want={"sword": 1}, now=LATER)
    neg.counter_offer(session_id="s1", proposer="alice",
                      give={"sword": 1}, want={"gold": 6}, now=LATER)
    with pytest.raises(tn.TradeError) as exc:
        neg.counter_offer(session_id="s1", proposer="bob", give={"gold": 2},
                          want={"sword": 1}, now=LATER)
    assert exc.value.code == "TRADE_VERSION_LIMIT"
    assert len(neg.versions_of("s1")) == 3


def test_invalid_inventory_and_offer_are_rejected():
    neg = negotiation()
    with pytest.raises(tn.TradeError) as exc:
        neg.set_inventory("alice", {"sword": -1})
    assert exc.value.code == "TRADE_INVALID_INVENTORY"
    for give, want in [({}, {"gold": 1}), ({"sword": 0}, {"gold": 1}),
                       ({"sword": 1}, {})]:
        with pytest.raises(tn.TradeError) as exc:
            neg.create_offer(session_id="s1", proposer="alice", give=give,
                             want=want, now=NOW)
        assert exc.value.code == "TRADE_INVALID_OFFER"
    assert neg.versions_of("s1") == []


def test_snapshot_is_detached_and_restore_roundtrips():
    neg = negotiation()
    open_offer(neg)
    neg.accept(session_id="s1", player="alice", version=1, now=LATER)
    state = neg.snapshot()
    assert set(state) == {"version", "sessions", "inventories", "stats"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["sessions"].clear()
    state["inventories"]["alice"]["sword"] = 99
    assert len(neg.versions_of("s1")) == 1
    assert neg.inventory_of("alice")["sword"] == 1

    clone = negotiation()
    clone.restore(pristine)
    assert len(clone.versions_of("s1")) == 1
    assert clone.accept(session_id="s1", player="bob", version=1,
                        now=LATER).status == "completed"
    assert clone.inventory_of("bob") == {"gold": 5, "sword": 1}


def test_restore_rejects_malformed_state_without_touching_data():
    neg = negotiation()
    open_offer(neg)
    good = neg.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, sessions=[]),
        {key: value for key, value in good.items() if key != "stats"},
    ]
    for bad in bad_states:
        with pytest.raises(tn.TradeError) as exc:
            neg.restore(bad)
        assert exc.value.code == "TRADE_SNAPSHOT_INVALID"
    assert len(neg.versions_of("s1")) == 1
    assert neg.state(session_id="s1", now=LATER) == "open"


def test_stats_track_offer_counter_and_pending_accept():
    neg = negotiation()
    open_offer(neg)
    neg.counter_offer(session_id="s1", proposer="bob", give={"gold": 4},
                      want={"sword": 1}, now=LATER)
    neg.accept(session_id="s1", player="alice", version=2, now=LATER)
    assert neg.stats() == {"sessions": 1, "offers": 1, "counters": 1,
                           "pending_accepts": 1, "accepted": 0,
                           "rejected": 0, "cancelled": 0, "expired": 0}
