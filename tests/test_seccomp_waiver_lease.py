import pytest

import seccomp_waiver_lease as swl


def lease(**kwargs):
    return swl.SeccompWaiverLease(**kwargs)


def test_request_creates_pending_waiver():
    lz = lease()
    lz.set_profile("p", {"open": "deny", "read": "allow"})
    w = lz.request("p", "open", "T-1", ttl=100, now=10)
    assert w.profile == "p"
    assert w.syscall == "open"
    assert w.ticket == "T-1"
    assert w.state == "pending"
    assert w.expires_at == 110
    assert w.renewals == 0
    assert lz.active("p") == []
    assert lz.stats()["requested"] == 1


def test_request_validation():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    with pytest.raises(swl.WaiverError) as exc:
        lz.request("missing", "open", "T-1", ttl=10, now=0)
    assert exc.value.code == "WAIVER_PROFILE_UNKNOWN"
    for ticket in ("", "   "):
        with pytest.raises(swl.WaiverError) as exc2:
            lz.request("p", "open", ticket, ttl=10, now=0)
        assert exc2.value.code == "WAIVER_TICKET_REQUIRED"
    for ttl in (0, -5):
        with pytest.raises(swl.WaiverError) as exc3:
            lz.request("p", "open", "T-1", ttl=ttl, now=0)
        assert exc3.value.code == "WAIVER_EXPIRY_INVALID"
    with pytest.raises(swl.WaiverError) as exc4:
        lz.request("p", "", "T-1", ttl=10, now=0)
    assert exc4.value.code == "WAIVER_SYSCALL_INVALID"


def test_approve_activates_and_effective_allows():
    lz = lease()
    lz.set_profile("p", {"open": "deny", "read": "allow"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    approved = lz.approve(w.waiver_id, now=5)
    assert approved.state == "active"
    assert lz.effective("p") == {"open": "allow", "read": "allow"}
    rows = lz.active("p")
    assert len(rows) == 1
    assert rows[0].waiver_id == w.waiver_id
    assert lz.stats()["approved"] == 1


def test_approve_requires_syscall_denied():
    lz = lease()
    lz.set_profile("p", {"read": "allow"})
    w = lz.request("p", "read", "T-1", ttl=100, now=0)
    with pytest.raises(swl.WaiverError) as exc:
        lz.approve(w.waiver_id, now=5)
    assert exc.value.code == "WAIVER_SYSCALL_ALLOWED"
    assert lz.find(ticket="T-1")[0].state == "pending"
    assert lz.effective("p") == {"read": "allow"}
    assert lz.active() == []


def test_duplicate_active_waiver_rejected():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    first = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(first.waiver_id, now=0)
    second = lz.request("p", "open", "T-2", ttl=100, now=0)
    with pytest.raises(swl.WaiverError) as exc:
        lz.approve(second.waiver_id, now=0)
    assert exc.value.code == "WAIVER_DUPLICATE"
    assert len(lz.active("p")) == 1


def test_sweep_restores_original_decision():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(w.waiver_id, now=0)
    assert lz.sweep(100) == 1
    assert lz.effective("p") == {"open": "deny"}
    assert lz.active() == []
    assert lz.stats()["expired"] == 1
    assert lz.sweep(200) == 0


def test_sweep_batch_cap():
    lz = lease(sweep_batch=1)
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    for index, syscall in enumerate(("open", "read")):
        w = lz.request("p", syscall, "T-%d" % index, ttl=100, now=0)
        lz.approve(w.waiver_id, now=0)
    assert lz.sweep(1000) == 1
    assert len(lz.active("p")) == 1
    assert lz.sweep(1000) == 1
    assert lz.active("p") == []


def test_revoke_restores_deny():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(w.waiver_id, now=0)
    revoked = lz.revoke(w.waiver_id, now=10)
    assert revoked.state == "revoked"
    assert lz.effective("p") == {"open": "deny"}
    with pytest.raises(swl.WaiverError) as exc:
        lz.revoke(w.waiver_id, now=20)
    assert exc.value.code == "WAIVER_STATE_INVALID"


def test_renew_extends_and_caps():
    lz = lease(max_renewals=2)
    lz.set_profile("p", {"open": "deny"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(w.waiver_id, now=0)
    first = lz.renew(w.waiver_id, extend=500, now=10)
    assert first.expires_at == 600
    assert first.renewals == 1
    second = lz.renew(w.waiver_id, extend=500, now=20)
    assert second.expires_at == 1100
    assert second.renewals == 2
    with pytest.raises(swl.WaiverError) as exc:
        lz.renew(w.waiver_id, extend=500, now=30)
    assert exc.value.code == "WAIVER_RENEW_LIMIT"
    assert lz.active("p")[0].expires_at == 1100
    assert lz.stats()["renewed"] == 2


def test_renew_must_extend():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(w.waiver_id, now=0)
    for extend in (0, -5):
        with pytest.raises(swl.WaiverError) as exc:
            lz.renew(w.waiver_id, extend=extend, now=10)
        assert exc.value.code == "WAIVER_RENEW_INVALID"
    assert lz.active("p")[0].expires_at == 100
    assert lz.active("p")[0].renewals == 0


def test_max_active_cap():
    lz = lease(max_active=1)
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    first = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(first.waiver_id, now=0)
    second = lz.request("p", "read", "T-2", ttl=100, now=0)
    with pytest.raises(swl.WaiverError) as exc:
        lz.approve(second.waiver_id, now=0)
    assert exc.value.code == "WAIVER_CAPACITY"
    assert len(lz.active("p")) == 1


def test_find_filters_and_sorts():
    lz = lease()
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    lz.set_profile("q", {"write": "deny"})
    handles = [
        lz.request("p", "read", "T-2", ttl=100, now=0),
        lz.request("p", "open", "T-1", ttl=100, now=0),
        lz.request("q", "write", "T-3", ttl=100, now=0),
    ]
    for handle in handles:
        lz.approve(handle.waiver_id, now=0)
    assert [row.ticket for row in lz.find(profile="p")] == ["T-1", "T-2"]
    assert [row.syscall for row in lz.find(profile="q")] == ["write"]
    assert [row.ticket for row in lz.find(ticket="T-3")] == ["T-3"]
    assert [row.ticket for row in lz.find(syscall="open")] == ["T-1"]
    assert len(lz.find()) == 3


def test_set_profile_replacement_keeps_waiver_effect():
    lz = lease()
    lz.set_profile("p", {"open": "deny"})
    w = lz.request("p", "open", "T-1", ttl=100, now=0)
    lz.approve(w.waiver_id, now=0)
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    assert lz.effective("p") == {"open": "allow", "read": "deny"}
    lz.revoke(w.waiver_id, now=10)
    assert lz.effective("p") == {"open": "deny", "read": "deny"}


def test_snapshot_restore_sweeps_expired():
    lz = lease()
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    short = lz.request("p", "open", "T-1", ttl=100, now=0)
    long = lz.request("p", "read", "T-2", ttl=1000, now=0)
    lz.approve(short.waiver_id, now=0)
    lz.approve(long.waiver_id, now=0)
    state = lz.snapshot()
    assert state["version"] == 1

    clone = lease()
    result = clone.restore(state, now=150)
    assert result == {"restored": 2, "swept": 1}
    assert [row.ticket for row in clone.active()] == ["T-2"]
    assert clone.effective("p") == {"open": "deny", "read": "allow"}


def test_stats_counters():
    lz = lease()
    lz.set_profile("p", {"open": "deny", "read": "deny"})
    first = lz.request("p", "open", "T-1", ttl=100, now=0)
    second = lz.request("p", "read", "T-2", ttl=100, now=0)
    lz.approve(first.waiver_id, now=0)
    lz.approve(second.waiver_id, now=0)
    lz.renew(first.waiver_id, extend=50, now=5)
    lz.revoke(second.waiver_id, now=6)
    stats = lz.stats()
    assert stats["requested"] == 2
    assert stats["approved"] == 2
    assert stats["renewed"] == 1
    assert stats["revoked"] == 1
    assert stats["expired"] == 0
    assert stats["restored"] == 0
