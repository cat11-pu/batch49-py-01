import threading

import pytest

import capability_delegation_ledger as cdl


def ledger(**kwargs):
    return cdl.CapabilityDelegationLedger(**kwargs)


def test_load_daemon_and_delegate_records_fields():
    obj = ledger()
    obj.load_daemon({"cap_a": 3})
    assert obj.summary() == {
        "roles": (),
        "capabilities": {"cap_a": {"daemon": 3, "delegated": 0,
                                   "exercisable": 0}},
        "records": {"active": 0, "pending_revoke": 0, "revoked": 0},
        "reclaimed": 0,
    }
    obj.register_holder("h1", pid=100, start=1000)
    rec = obj.delegate(role="ops", capability="cap_a", holder="h1",
                       count=2, ttl=60, now=10)
    assert rec.role == "ops"
    assert rec.capability == "cap_a"
    assert rec.holder == "h1"
    assert rec.count == 2
    assert rec.granted_by == "daemon"
    assert rec.granted_at == 10
    assert rec.expires_at == 70
    assert rec.state == "active"
    assert obj.held_by("h1") == [rec]


def test_delegate_cannot_exceed_daemon_pool():
    obj = ledger()
    obj.load_daemon({"cap_a": 1})
    obj.register_holder("h1", pid=1, start=1)
    obj.register_holder("h2", pid=2, start=2)
    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="ops", capability="cap_a", holder="h1", count=2)
    assert exc.value.code == "CAP_UNAVAILABLE"
    assert obj.active("cap_a") == 0

    obj.delegate(role="ops", capability="cap_a", holder="h1", count=1)
    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="ops", capability="cap_a", holder="h2", count=1)
    assert exc.value.code == "CAP_UNAVAILABLE"
    assert obj.active("cap_a") == 1


def test_duplicate_delegate_is_idempotent():
    obj = ledger()
    obj.load_daemon({"cap_a": 4})
    obj.register_holder("h1", pid=1, start=1)
    first = obj.delegate(role="ops", capability="cap_a", holder="h1",
                         count=2, now=10)
    second = obj.delegate(role="ops", capability="cap_a", holder="h1",
                          count=2, now=20)
    assert second.count == 2
    assert second.granted_at == 10
    assert second == first
    assert obj.active("cap_a") == 2
    assert obj.stats() == {"delegated": 1, "suppressed": 1, "pending_revoke": 0,
                           "revoked": 0, "reclaimed": 0, "expired": 0}


def test_per_holder_capability_limit():
    obj = ledger(max_caps_per_holder=2)
    obj.load_daemon({"cap_a": 2, "cap_b": 2, "cap_c": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1")
    obj.delegate(role="ops", capability="cap_b", holder="h1")
    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="ops", capability="cap_c", holder="h1")
    assert exc.value.code == "HOLDER_LIMIT"
    assert [rec.capability for rec in obj.held_by("h1")] == ["cap_a", "cap_b"]


def test_global_delegation_record_limit():
    obj = ledger(max_delegations=2)
    obj.load_daemon({"cap_a": 5, "cap_b": 5, "cap_c": 5})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1")
    obj.delegate(role="ops", capability="cap_b", holder="h1")
    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="ops", capability="cap_c", holder="h1")
    assert exc.value.code == "DELEGATION_LIMIT"
    assert len(obj.held_by("h1")) == 2


def test_role_count_limit():
    obj = ledger(max_roles=2)
    obj.load_daemon({"cap_a": 2, "cap_b": 2, "cap_c": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.register_holder("h2", pid=2, start=2)
    obj.register_holder("h3", pid=3, start=3)
    obj.delegate(role="ops", capability="cap_a", holder="h1")
    obj.delegate(role="audit", capability="cap_b", holder="h2")
    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="dev", capability="cap_c", holder="h3")
    assert exc.value.code == "ROLE_LIMIT"
    assert obj.held_by("h3") == []


def test_active_units_and_summary_view():
    obj = ledger()
    obj.load_daemon({"cap_a": 4, "cap_b": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.register_holder("h2", pid=2, start=2)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=2)
    obj.delegate(role="audit", capability="cap_b", holder="h2", count=1)
    assert obj.active("cap_a") == 2
    assert obj.active("cap_b") == 1
    assert obj.active("cap_missing") == 0
    assert obj.summary() == {
        "roles": ("audit", "ops"),
        "capabilities": {
            "cap_a": {"daemon": 4, "delegated": 2, "exercisable": 2},
            "cap_b": {"daemon": 2, "delegated": 1, "exercisable": 1},
        },
        "records": {"active": 2, "pending_revoke": 0, "revoked": 0},
        "reclaimed": 0,
    }


def test_revoke_while_holder_alive_marks_pending():
    obj = ledger()
    obj.load_daemon({"cap_a": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.register_holder("h2", pid=2, start=2)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=2)
    assert obj.revoke(role="ops", capability="cap_a", holder="h1", now=5) == 0
    assert obj.held_by("h1")[0].state == "pending_revoke"
    assert obj.active("cap_a") == 0
    view = obj.summary()
    assert view["capabilities"]["cap_a"] == {"daemon": 2, "delegated": 2,
                                             "exercisable": 0}
    assert view["records"] == {"active": 0, "pending_revoke": 1, "revoked": 0}
    assert view["reclaimed"] == 0

    with pytest.raises(cdl.CapError) as exc:
        obj.delegate(role="ops", capability="cap_a", holder="h2", count=1)
    assert exc.value.code == "CAP_UNAVAILABLE"

    assert obj.exit_holder("h1", now=6) == 1
    view = obj.summary()
    assert view["capabilities"]["cap_a"] == {"daemon": 2, "delegated": 0,
                                             "exercisable": 0}
    assert view["records"] == {"active": 0, "pending_revoke": 0, "revoked": 1}
    assert view["reclaimed"] == 2


def test_revoke_missing_or_repeated_does_not_double_reclaim():
    obj = ledger()
    obj.load_daemon({"cap_a": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=1)
    assert obj.revoke(role="ops", capability="cap_b", holder="h1", now=1) == 0
    assert obj.exit_holder("h1", now=2) == 1
    assert obj.revoke(role="ops", capability="cap_a", holder="h1", now=3) == 0
    assert obj.revoke(role="ops", capability="cap_a", holder="h1", now=4) == 0
    assert obj.summary()["reclaimed"] == 1
    assert obj.stats()["revoked"] == 1


def test_exit_holder_reclaims_all_units():
    obj = ledger()
    obj.load_daemon({"cap_a": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=2, now=0)
    assert obj.exit_holder("h1", now=5) == 1
    assert obj.held_by("h1")[0].state == "revoked"
    assert obj.active("cap_a") == 0
    assert obj.summary()["reclaimed"] == 2

    obj.register_holder("h2", pid=2, start=2)
    rec = obj.delegate(role="ops", capability="cap_a", holder="h2", count=2,
                       now=6)
    assert rec.state == "active"
    assert obj.active("cap_a") == 2


def test_pid_reuse_reclaims_previous_holder():
    obj = ledger()
    obj.load_daemon({"cap_a": 4})
    obj.register_holder("h-old", pid=10, start=100)
    obj.delegate(role="ops", capability="cap_a", holder="h-old", count=1, now=0)
    obj.register_holder("h-new", pid=10, start=200)
    report = obj.tick(now=1)
    assert report == {"expired": 0, "reclaimed": 1, "dead_holders": 1}
    assert obj.held_by("h-old")[0].state == "revoked"
    assert obj.held_by("h-new") == []
    assert obj.active("cap_a") == 0
    fresh = obj.delegate(role="ops", capability="cap_a", holder="h-new",
                         count=1, now=2)
    assert fresh.state == "active"


def test_tick_expires_delegations_by_deadline():
    obj = ledger()
    obj.load_daemon({"cap_a": 4})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=2,
                 ttl=30, now=100)
    assert obj.tick(now=129) == {"expired": 0, "reclaimed": 0,
                                 "dead_holders": 0}
    assert obj.active("cap_a") == 2
    report = obj.tick(now=130)
    assert report == {"expired": 1, "reclaimed": 2, "dead_holders": 0}
    assert obj.held_by("h1")[0].state == "revoked"
    assert obj.active("cap_a") == 0
    assert obj.stats()["expired"] == 1


def test_snapshot_restore_roundtrip_keeps_revoked_records():
    obj = ledger()
    obj.load_daemon({"cap_a": 4})
    obj.register_holder("h1", pid=1, start=1)
    obj.register_holder("h2", pid=2, start=2)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=2, now=10)
    obj.delegate(role="audit", capability="cap_a", holder="h2", count=1, now=11)
    obj.revoke(role="audit", capability="cap_a", holder="h2", now=12)
    obj.exit_holder("h2", now=13)
    state = obj.snapshot()
    assert set(state) == {"version", "daemon", "holders", "delegations",
                          "counters"}
    assert state["version"] == 1

    clone = ledger()
    clone.restore(state)
    assert clone.summary() == obj.summary()
    assert [(rec.holder, rec.state) for rec in clone.held_by("h2")] == [
        ("h2", "revoked")]
    assert clone.active("cap_a") == 2
    assert clone.stats() == obj.stats()


def test_restore_rejects_malformed_state_without_touching_live_data():
    obj = ledger()
    obj.load_daemon({"cap_a": 2})
    obj.register_holder("h1", pid=1, start=1)
    obj.delegate(role="ops", capability="cap_a", holder="h1", count=1, now=10)
    before = obj.summary()
    bad_states = [
        None,
        {},
        {"version": 2, "daemon": {}, "holders": {}, "delegations": [],
         "counters": {}},
        {"version": 1, "daemon": [], "holders": {}, "delegations": [],
         "counters": {}},
        {"version": 1, "daemon": {"cap_a": 2}, "holders": {},
         "delegations": "nope", "counters": {}},
    ]
    for bad in bad_states:
        with pytest.raises(cdl.CapError) as exc:
            obj.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert obj.summary() == before
    assert obj.active("cap_a") == 1


def test_concurrent_delegations_never_overshoot_the_pool():
    obj = ledger()
    caps = {f"cap-{index}": 4 for index in range(8)}
    obj.load_daemon(caps)
    for index in range(8):
        obj.register_holder(f"h{index}", pid=100 + index, start=1000)
    errors = []
    barrier = threading.Barrier(8)

    def worker(index):
        capability = f"cap-{index}"
        try:
            barrier.wait(timeout=5)
            obj.delegate(role="ops", capability=capability,
                         holder=f"h{index}", count=4, now=10)
            observed = obj.summary()["capabilities"][capability]["delegated"]
            if not 0 <= observed <= 4:
                errors.append(f"delegated {observed}")
            if obj.active(capability) > 4:
                errors.append("active overshoot")
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,))
               for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for capability in caps:
        assert obj.active(capability) == 4
    assert obj.summary()["records"]["active"] == 8
    assert sum(obj.active(capability) for capability in caps) == 32
