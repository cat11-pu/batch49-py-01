import copy

import pytest

import sso_logout_ticket_broker as sso

NOW = 1000
TTL_END = 1600


def broker(**kwargs):
    return sso.SsoLogoutTicketBroker(**kwargs)


def issue(owner, rps, *, user="u1", session_id="s1", now=NOW, ttl=None):
    return owner.issue_ticket(user=user, session_id=session_id, rps=rps,
                              now=now, ttl=ttl)


def test_issue_ticket_dedupes_rps_and_sets_expiry():
    owner = broker()
    ticket = issue(owner, ["rp-b", "rp-a", "rp-a"])
    assert ticket.ticket_id == "logout-0001"
    assert ticket.user == "u1"
    assert ticket.session_id == "s1"
    assert ticket.rps == ("rp-a", "rp-b")
    assert ticket.status == "open"
    assert ticket.issued_at == NOW
    assert ticket.expires_at == NOW + 600
    assert owner.stats()["issued"] == 1


def test_issue_ticket_rejects_blank_identity_and_empty_rps():
    owner = broker()
    bad_calls = [
        dict(user="", session_id="s1", rps=["rp-a"], now=NOW),
        dict(user="   ", session_id="s1", rps=["rp-a"], now=NOW),
        dict(user="u1", session_id="", rps=["rp-a"], now=NOW),
        dict(user="u1", session_id="s1", rps=[], now=NOW),
        dict(user="u1", session_id="s1", rps=["rp-a", "  "], now=NOW),
    ]
    for kwargs in bad_calls:
        with pytest.raises(sso.SsoLogoutError) as exc:
            owner.issue_ticket(**kwargs)
        assert exc.value.code == "LOGOUT_INVALID_REQUEST"
    assert owner.stats()["issued"] == 0


def test_successful_delivery_marks_rp_delivered():
    owner = broker()
    ticket = issue(owner, ["rp-a", "rp-b"])
    result = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 1,
                           success=True)
    assert result.status == "delivered"
    assert result.attempts == 1
    assert result.next_attempt_at is None
    assert result.reason is None
    assert owner.get_ticket(ticket.ticket_id).status == "open"
    assert owner.stats()["delivered"] == 1
    assert owner.stats()["pending"] == 0
    assert owner.pending() == ()


def test_repeated_delivery_to_same_rp_is_idempotent():
    owner = broker()
    ticket = issue(owner, ["rp-a"])
    first = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW,
                          success=True)
    second = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 5,
                           success=True)
    assert first.status == "delivered"
    assert second.status == "duplicate"
    assert second.attempts == 1
    assert owner.stats()["delivered"] == 1
    assert owner.stats()["failed"] == 0


def test_failed_delivery_schedules_backoff_and_keeps_reason():
    owner = broker()
    ticket = issue(owner, ["rp-a"])
    result = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW,
                           success=False, reason="http_500")
    assert result.status == "pending"
    assert result.attempts == 1
    assert result.next_attempt_at == NOW + 30
    assert result.reason == "http_500"

    queued = owner.pending()
    assert len(queued) == 1
    assert queued[0].ticket_id == ticket.ticket_id
    assert queued[0].rp == "rp-a"
    assert queued[0].attempts == 1
    assert queued[0].next_attempt_at == NOW + 30
    assert queued[0].last_reason == "http_500"
    assert owner.stats()["failed"] == 1
    assert owner.stats()["pending"] == 1


def test_retry_pending_returns_items_in_due_order():
    owner = broker()
    first = issue(owner, ["rp-b"], user="u1", session_id="s1")
    second = issue(owner, ["rp-a"], user="u2", session_id="s2")
    owner.deliver(ticket_id=first.ticket_id, rp="rp-b", now=NOW,
                  success=False, reason="timeout")
    owner.deliver(ticket_id=second.ticket_id, rp="rp-a", now=NOW,
                  success=False, reason="dns")

    assert owner.retry_pending(NOW + 29) == ()
    due = owner.retry_pending(NOW + 30)
    assert [(row.ticket_id, row.rp, row.attempts) for row in due] == [
        ("logout-0001", "rp-b", 1),
        ("logout-0002", "rp-a", 1),
    ]
    assert [row.last_reason for row in due] == ["timeout", "dns"]


def test_retry_success_clears_backoff_and_counts_retry():
    owner = broker()
    ticket = issue(owner, ["rp-a"])
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW,
                  success=False, reason="timeout")
    retried = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a",
                            now=NOW + 30, success=True)
    assert retried.status == "delivered"
    assert retried.attempts == 2
    assert retried.next_attempt_at is None
    assert owner.pending() == ()
    assert owner.stats()["failed"] == 1
    assert owner.stats()["delivered"] == 1
    assert owner.stats()["retried"] == 1
    assert owner.stats()["pending"] == 0


def test_ack_removes_pending_entry_and_is_idempotent():
    owner = broker()
    ticket = issue(owner, ["rp-a"])
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW,
                  success=False, reason="timeout")
    assert owner.ack(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 1) is True
    assert owner.pending() == ()
    assert owner.stats()["acked"] == 1
    assert owner.stats()["pending"] == 0
    assert owner.ack(ticket_id=ticket.ticket_id, rp="rp-a",
                     now=NOW + 2) is False


def test_queue_capacity_per_rp_refuses_new_pending_entry():
    owner = broker(max_queue_per_rp=1)
    first = issue(owner, ["rp-x"], user="u1", session_id="s1")
    owner.deliver(ticket_id=first.ticket_id, rp="rp-x", now=NOW,
                  success=False, reason="timeout")

    second = issue(owner, ["rp-x"], user="u2", session_id="s2")
    with pytest.raises(sso.SsoLogoutError) as exc:
        owner.deliver(ticket_id=second.ticket_id, rp="rp-x", now=NOW + 1,
                      success=False, reason="timeout")
    assert exc.value.code == "LOGOUT_QUEUE_FULL"
    assert len(owner.pending("rp-x")) == 1
    assert owner.stats()["failed"] == 1
    assert owner.stats()["pending"] == 1


def test_expire_closes_tickets_at_deadline_and_drops_queue():
    owner = broker()
    ticket = issue(owner, ["rp-a", "rp-b"], ttl=600)
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 100,
                  success=False, reason="timeout")
    assert owner.expire(TTL_END - 1) == ()
    assert owner.get_ticket(ticket.ticket_id).status == "open"
    assert owner.expire(TTL_END) == (ticket.ticket_id,)
    assert owner.get_ticket(ticket.ticket_id).status == "expired"
    assert owner.pending() == ()
    assert owner.expire(TTL_END + 1) == ()
    assert owner.stats()["expired"] == 1


def test_finished_ledger_does_not_reuse_ticket_id():
    owner = broker()
    first = issue(owner, ["rp-a"], user="u1", session_id="s1")
    owner.deliver(ticket_id=first.ticket_id, rp="rp-a", now=NOW, success=True)
    owner.ack(ticket_id=first.ticket_id, rp="rp-a", now=NOW + 1)

    second = issue(owner, ["rp-a"], user="u1", session_id="s2")
    assert second.ticket_id == "logout-0002"
    assert first.ticket_id == "logout-0001"
    assert owner.stats()["issued"] == 2


def test_unknown_ticket_and_unknown_rp_are_rejected():
    owner = broker()
    ticket = issue(owner, ["rp-a"])
    for call in (
        lambda: owner.deliver(ticket_id="logout-9999", rp="rp-a", now=NOW,
                              success=True),
        lambda: owner.ack(ticket_id="logout-9999", rp="rp-a", now=NOW),
    ):
        with pytest.raises(sso.SsoLogoutError) as exc:
            call()
        assert exc.value.code == "LOGOUT_TICKET_NOT_FOUND"
    for call in (
        lambda: owner.deliver(ticket_id=ticket.ticket_id, rp="rp-z", now=NOW,
                              success=True),
        lambda: owner.ack(ticket_id=ticket.ticket_id, rp="rp-z", now=NOW),
    ):
        with pytest.raises(sso.SsoLogoutError) as exc:
            call()
        assert exc.value.code == "LOGOUT_RP_NOT_ON_TICKET"


def test_snapshot_is_detached_and_restore_roundtrips():
    owner = broker()
    ticket = issue(owner, ["rp-a", "rp-b"])
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 1,
                  success=True)
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-b", now=NOW + 2,
                  success=False, reason="timeout")
    state = owner.snapshot()
    assert set(state) == {"version", "tickets", "deliveries", "stats"}
    pristine = copy.deepcopy(state)

    state["tickets"].clear()
    state["stats"]["issued"] = 99
    assert owner.get_ticket(ticket.ticket_id).status == "open"
    assert len(owner.pending()) == 1
    assert owner.stats()["issued"] == 1

    clone = broker()
    clone.restore(pristine)
    assert clone.get_ticket(ticket.ticket_id).status == "open"
    restored = clone.pending()
    assert len(restored) == 1
    assert restored[0].rp == "rp-b"
    assert restored[0].attempts == 1
    assert clone.stats()["delivered"] == 1
    retried = clone.deliver(ticket_id=ticket.ticket_id, rp="rp-b",
                            now=NOW + 32, success=True)
    assert retried.status == "delivered"
    assert clone.stats()["retried"] == 1


def test_stats_track_issue_deliver_retry_ack_and_expire():
    owner = broker()
    ticket = issue(owner, ["rp-a", "rp-b"])
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 1,
                  success=True)
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-b", now=NOW + 2,
                  success=False, reason="timeout")
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-b", now=NOW + 32,
                  success=True)
    owner.ack(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 33)
    issue(owner, ["rp-c"], user="u2", session_id="s2", now=NOW + 40)
    owner.expire(TTL_END)

    assert owner.stats() == {"issued": 2, "delivered": 2, "failed": 1,
                             "retried": 1, "acked": 1, "expired": 1,
                             "abandoned": 0, "pending": 0}


def test_one_rp_failure_does_not_affect_other_rps():
    owner = broker()
    ticket = issue(owner, ["rp-a", "rp-b"])
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW, success=True)
    owner.deliver(ticket_id=ticket.ticket_id, rp="rp-b", now=NOW,
                  success=False, reason="timeout")

    assert owner.stats()["delivered"] == 1
    assert owner.stats()["failed"] == 1
    due = owner.retry_pending(NOW + 30)
    assert [(row.ticket_id, row.rp) for row in due] == [
        (ticket.ticket_id, "rp-b")]
    again = owner.deliver(ticket_id=ticket.ticket_id, rp="rp-a", now=NOW + 31,
                          success=True)
    assert again.status == "duplicate"
    assert again.attempts == 1
