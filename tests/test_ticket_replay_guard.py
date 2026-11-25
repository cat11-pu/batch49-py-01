import threading

import pytest

import ticket_replay_guard as tg

SECRET = "s" * 24
SECRET2 = "t" * 24


def guard(**kwargs):
    g = tg.TicketReplayGuard(**kwargs)
    g.add_key(SECRET)
    return g


def rotated_guard(**kwargs):
    g = tg.TicketReplayGuard(**kwargs)
    g.add_key(SECRET)
    g.add_key(SECRET2)
    return g


def test_mint_returns_sequential_tickets_on_the_active_epoch():
    g = guard()
    first = g.mint("alice", now=100)
    assert first["ticket_id"] == "tk-1"
    assert first["subject"] == "alice"
    assert first["epoch"] == 1
    assert first["issued_at"] == 100
    assert first["expires_at"] == 400
    assert first["reusable"] is False
    parts = first["token"].split(".")
    assert len(parts) == 4
    assert parts[0] == "v1"
    assert parts[1] == "1"
    second = g.mint("bob", now=101, ttl=30)
    assert second["ticket_id"] == "tk-2"
    assert second["expires_at"] == 131
    assert g.active_epoch() == 1


def test_mint_rejects_empty_subject_and_out_of_range_ttl():
    g = guard()
    with pytest.raises(tg.TicketReplayError) as exc:
        g.mint("   ", now=10)
    assert exc.value.code == "TICKET_INVALID"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.mint("alice", now=10, ttl=0)
    assert exc.value.code == "TICKET_TTL_INVALID"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.mint("alice", now=10, ttl=g.ticket_lifetime + 1)
    assert exc.value.code == "TICKET_TTL_INVALID"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.mint("alice", now=10, client_binding="")
    assert exc.value.code == "TICKET_INVALID"
    assert g.stats()["minted"] == 0


def test_key_rotation_bumps_epoch_and_keyring_is_capped():
    g = tg.TicketReplayGuard(max_keys=2)
    with pytest.raises(tg.TicketReplayError) as exc:
        g.add_key("short")
    assert exc.value.code == "TICKET_KEY_INVALID"
    assert g.add_key(SECRET) == 1
    assert g.mint("alice", now=10)["epoch"] == 1
    assert g.add_key(SECRET2) == 2
    assert g.active_epoch() == 2
    assert g.mint("alice", now=11)["epoch"] == 2
    with pytest.raises(tg.TicketReplayError) as exc:
        g.add_key("u" * 24)
    assert exc.value.code == "TICKET_KEYRING_FULL"
    assert len(g.keys()) == 2


def test_retire_marks_key_and_refuses_active_or_unknown():
    g = rotated_guard()
    entries = g.keys()
    assert [item["epoch"] for item in entries] == [1, 2]
    assert entries[0]["active"] is False
    assert entries[1]["active"] is True
    assert g.retire_key(1, now=50) is True
    assert g.retire_key(1, now=60) is False
    with pytest.raises(tg.TicketReplayError) as exc:
        g.retire_key(2, now=70)
    assert exc.value.code == "TICKET_KEY_ACTIVE"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.retire_key(9, now=70)
    assert exc.value.code == "TICKET_KEY_UNKNOWN"
    assert g.keys()[0]["retired_at"] == 50
    assert g.stats()["retired"] == 1


def test_unknown_key_epoch_is_refused_before_payload_use():
    g = guard()
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem("v1.7.e30.deadbeef", now=100)
    assert exc.value.code == "TICKET_KEY_UNKNOWN"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem("not-a-ticket", now=100)
    assert exc.value.code == "TICKET_TOKEN_INVALID"
    assert g.stats()["rejected"] == 2


def test_tampered_mac_is_refused():
    g = guard()
    ticket = g.mint("alice", now=100)
    token = ticket["token"]
    replacement = "a" if token[-1] != "a" else "b"
    forged = token[:-1] + replacement
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem(forged, now=101)
    assert exc.value.code == "TICKET_MAC_INVALID"
    assert g.stats()["rejected"] == 1
    assert g.ledger_size() == 0


def test_ticket_expires_at_its_deadline():
    g = guard()
    ticket = g.mint("alice", now=100, ttl=50)
    out = g.redeem(ticket["token"], now=149)
    assert out["expires_at"] == 150
    assert out["subject"] == "alice"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem(ticket["token"], now=150)
    assert exc.value.code == "TICKET_EXPIRED"
    assert g.stats()["replayed"] == 0


def test_single_use_ticket_cannot_be_redeemed_twice():
    g = guard()
    ticket = g.mint("alice", now=100)
    first = g.redeem(ticket["token"], now=110)
    assert first["subject"] == "alice"
    assert first["early_data"] is False
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem(ticket["token"], now=111)
    assert exc.value.code == "TICKET_REPLAYED"
    assert g.stats()["redeemed"] == 1
    assert g.stats()["replayed"] == 1
    assert g.ledger_size() == 1


def test_reusable_ticket_reports_early_data_only_inside_the_window():
    g = guard(replay_window=60)
    early = g.mint("alice", now=100, reusable=True)
    first = g.redeem(early["token"], now=160)
    assert first["reusable"] is True
    assert first["early_data"] is True
    late = g.mint("alice", now=100, reusable=True)
    second = g.redeem(late["token"], now=161)
    assert second["early_data"] is False
    assert second["subject"] == "alice"
    third = g.redeem(late["token"], now=162)
    assert third["early_data"] is False
    assert g.stats()["replayed"] == 0


def test_binding_mismatch_is_refused_without_burning_the_ticket():
    g = guard()
    ticket = g.mint("alice", now=100, client_binding="cert-fp-1")
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem(ticket["token"], now=101, client_binding="cert-fp-2")
    assert exc.value.code == "TICKET_BINDING_MISMATCH"
    with pytest.raises(tg.TicketReplayError) as exc:
        g.redeem(ticket["token"], now=101)
    assert exc.value.code == "TICKET_BINDING_MISMATCH"
    out = g.redeem(ticket["token"], now=102, client_binding="cert-fp-1")
    assert out["subject"] == "alice"
    assert g.stats()["rejected"] == 2


def test_prune_drops_only_expired_ledger_entries():
    g = guard()
    short = g.mint("alice", now=100, ttl=10)
    long = g.mint("bob", now=100, ttl=100)
    g.redeem(short["token"], now=105)
    g.redeem(long["token"], now=105)
    assert g.ledger_size() == 2
    assert g.prune(now=110) == 1
    assert g.ledger_size() == 1
    assert g.prune(now=110) == 0
    assert g.stats()["pruned"] == 1


def test_snapshot_is_detached_and_restore_rolls_back():
    g = guard()
    ticket = g.mint("alice", now=100)
    snap = g.snapshot()
    assert snap["stats"]["minted"] == 1
    assert snap["ledger"] == {}
    g.redeem(ticket["token"], now=101)
    g.mint("bob", now=102)
    assert snap["stats"]["minted"] == 1
    assert snap["ledger"] == {}
    g.restore(snap)
    out = g.redeem(ticket["token"], now=103)
    assert out["subject"] == "alice"
    assert g.stats()["minted"] == 1


def test_restore_rejects_malformed_state_without_touching_the_guard():
    g = guard()
    g.mint("alice", now=100)
    with pytest.raises(tg.TicketReplayError) as exc:
        g.restore({"keys": "nope"})
    assert exc.value.code == "TICKET_SNAPSHOT_INVALID"
    assert g.active_epoch() == 1
    assert g.stats()["minted"] == 1
    assert len(g.keys()) == 1


def test_concurrent_redemption_admits_exactly_one_use():
    g = guard()
    ticket = g.mint("alice", now=100)
    accepted = []
    refused = []
    guard_lock = threading.Lock()

    def worker():
        try:
            out = g.redeem(ticket["token"], now=105)
        except tg.TicketReplayError as exc:
            with guard_lock:
                refused.append(exc.code)
        else:
            with guard_lock:
                accepted.append(out["ticket_id"])

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert accepted == ["tk-1"]
    assert sorted(refused) == ["TICKET_REPLAYED"] * 7
    assert g.ledger_size() == 1


def test_stats_track_issue_redemption_and_rejection():
    g = guard()
    first = g.mint("alice", now=100)
    g.mint("bob", now=100)
    g.redeem(first["token"], now=101)
    with pytest.raises(tg.TicketReplayError):
        g.redeem(first["token"], now=102)
    with pytest.raises(tg.TicketReplayError):
        g.redeem("v1.1.bad.bad", now=102)
    st = g.stats()
    assert st["minted"] == 2
    assert st["redeemed"] == 1
    assert st["replayed"] == 1
    assert st["rejected"] == 2
    assert st["pruned"] == 0
    assert g.ledger_size() == 1
    assert len(g.keys()) == 1
