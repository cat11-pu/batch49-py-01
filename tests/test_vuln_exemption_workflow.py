import copy
import threading

import pytest

import vuln_exemption_workflow as vw

NOW = "2026-11-25T09:00:00+00:00"
NEXT = "2026-11-25T09:30:00+00:00"
DEFAULT_EXPIRY = "2026-12-02T09:00:00+00:00"


def workflow(**kwargs):
    return vw.VulnExemptionWorkflow(**kwargs)


def request(wf, exemption_id="EX-1", rule_id="RULE-1", tags=("pci",),
            user="alice", ttl=None, now=NOW):
    return wf.request_exemption(exemption_id, rule_id, list(tags), user,
                                ttl_seconds=ttl, now=now)


def active(wf, exemption_id="EX-1", rule_id="RULE-1", tags=("pci",),
           user="alice", approver="bob", now=NOW):
    request(wf, exemption_id, rule_id, tags, user, now=now)
    wf.approve_exemption(exemption_id, approver, now=now)
    return wf.activate_exemption(exemption_id, now=now)


def test_request_normalizes_tags_and_starts_requested_state():
    wf = workflow()
    record = request(wf, tags=("prod", "pci", "prod"))
    assert record.exemption_id == "EX-1"
    assert record.version == 1
    assert record.rule_id == "RULE-1"
    assert record.asset_tags == ("pci", "prod")
    assert record.state == "requested"
    assert record.requested_by == "alice"
    assert record.approved_by is None
    assert record.created_at == NOW
    assert record.activated_at is None
    assert record.expires_at is None
    assert record.superseded_by is None
    assert wf.stats()["requested"] == 1
    assert wf.list_by_state("requested") == [record]


def test_request_rejects_invalid_payload_and_duplicate_identifier():
    wf = workflow()
    bad_calls = [
        ("", "RULE-1", ["pci"], "alice", None),
        ("EX-1", "", ["pci"], "alice", None),
        ("EX-1", "RULE-1", [], "alice", None),
        ("EX-1", "RULE-1", ["pci"], "", None),
        ("EX-1", "RULE-1", ["pci"], "alice", 0),
    ]
    for args in bad_calls:
        with pytest.raises(vw.ExemptionError) as exc:
            wf.request_exemption(*args, now=NOW)
        assert exc.value.code == "EXEMPTION_INVALID_REQUEST"
    request(wf)
    with pytest.raises(vw.ExemptionError) as exc:
        request(wf)
    assert exc.value.code == "EXEMPTION_DUPLICATE_ID"
    assert wf.stats()["requested"] == 1


def test_approve_requested_records_actor_and_history():
    wf = workflow()
    request(wf)
    approved = wf.approve_exemption("EX-1", "bob", now=NEXT)
    assert approved.state == "approved"
    assert approved.approved_by == "bob"
    assert approved.activated_at is None
    assert approved.expires_at is None
    events = wf.history("EX-1")
    assert [(event["from"], event["to"], event["actor"]) for event in events] == [
        (None, "requested", "alice"),
        ("requested", "approved", "bob"),
    ]
    assert wf.stats()["approved"] == 1


def test_activate_computes_expiry_from_default_ttl():
    wf = workflow()
    request(wf)
    wf.approve_exemption("EX-1", "bob", now=NOW)
    activated = wf.activate_exemption("EX-1", now=NOW)
    assert activated.state == "active"
    assert activated.activated_at == NOW
    assert activated.expires_at == DEFAULT_EXPIRY
    assert activated.approved_by == "bob"
    events = wf.history("EX-1")
    assert (events[-1]["from"], events[-1]["to"], events[-1]["actor"]) == (
        "approved", "active", "system")
    assert wf.stats()["activated"] == 1


def test_active_exemption_applies_to_declared_rule_and_tag_subset():
    wf = workflow()
    asset = wf.register_asset("asset-1", ["prod", "pci"], now=NOW)
    assert asset.tags == ("pci", "prod")
    assert asset.updated_at == NOW
    active(wf, tags=("pci",))
    assert wf.is_exempted("RULE-1", "asset-1", now=NOW) is True
    assert wf.is_exempted("RULE-2", "asset-1", now=NOW) is False
    assert wf.is_exempted("RULE-1", "asset-2", now=NOW) is False
    wf.register_asset("asset-2", ["prod"], now=NOW)
    assert wf.is_exempted("RULE-1", "asset-2", now=NOW) is False


def test_scope_requires_every_declared_tag():
    wf = workflow()
    wf.register_asset("asset-1", ["pci", "prod"], now=NOW)
    active(wf, "EX-1", "RULE-1", ("pci", "critical"))
    assert wf.is_exempted("RULE-1", "asset-1", now=NOW) is False
    request(wf, "EX-2", "RULE-1", ("prod", "pci"))
    wf.approve_exemption("EX-2", "bob", now=NOW)
    wf.activate_exemption("EX-2", now=NOW)
    assert wf.is_exempted("RULE-1", "asset-1", now=NOW) is True


def test_invalid_transition_reports_current_and_allowed_states():
    wf = workflow()
    request(wf)
    with pytest.raises(vw.ExemptionError) as exc:
        wf.activate_exemption("EX-1", now=NOW)
    assert exc.value.code == "EXEMPTION_INVALID_TRANSITION"
    assert exc.value.detail["current"] == "requested"
    assert set(exc.value.detail["allowed"]) == {"approved", "rejected", "revoked"}
    assert wf.get_exemption("EX-1").state == "requested"
    wf.approve_exemption("EX-1", "bob", now=NOW)
    wf.activate_exemption("EX-1", now=NOW)
    with pytest.raises(vw.ExemptionError) as exc:
        wf.approve_exemption("EX-1", "bob", now=NEXT)
    assert exc.value.code == "EXEMPTION_INVALID_TRANSITION"
    assert exc.value.detail["current"] == "active"
    assert "revoked" in exc.value.detail["allowed"]


def test_reject_is_terminal_and_requires_reason():
    wf = workflow()
    request(wf)
    with pytest.raises(vw.ExemptionError) as exc:
        wf.reject_exemption("EX-1", "bob", "", now=NOW)
    assert exc.value.code == "EXEMPTION_INVALID_REQUEST"
    rejected = wf.reject_exemption("EX-1", "bob", "duplicate request", now=NOW)
    assert rejected.state == "rejected"
    assert rejected.reason == "duplicate request"
    assert rejected.approved_by is None
    assert wf.stats()["rejected"] == 1
    for call in (
        lambda: wf.approve_exemption("EX-1", "bob", now=NOW),
        lambda: wf.activate_exemption("EX-1", now=NOW),
        lambda: wf.renew_exemption("EX-1", 60, "bob", now=NOW),
    ):
        with pytest.raises(vw.ExemptionError) as exc:
            call()
        assert exc.value.code == "EXEMPTION_INVALID_TRANSITION"


def test_revoke_active_exemption_blocks_further_use():
    wf = workflow()
    wf.register_asset("asset-1", ["pci"], now=NOW)
    active(wf)
    revoked = wf.revoke_exemption("EX-1", "scope changed", now=NEXT)
    assert revoked.state == "revoked"
    assert revoked.reason == "scope changed"
    assert wf.is_exempted("RULE-1", "asset-1", now=NEXT) is False
    assert wf.stats()["revoked"] == 1
    assert wf.versions("EX-1")[-1].state == "revoked"
    for call in (
        lambda: wf.approve_exemption("EX-1", "bob", now=NEXT),
        lambda: wf.activate_exemption("EX-1", now=NEXT),
        lambda: wf.renew_exemption("EX-1", 60, "bob", now=NEXT),
    ):
        with pytest.raises(vw.ExemptionError) as exc:
            call()
        assert exc.value.code == "EXEMPTION_INVALID_TRANSITION"


def test_expiry_is_effective_at_exact_expires_at():
    wf = workflow()
    wf.register_asset("asset-1", ["pci"], now=NOW)
    active(wf)
    before = "2026-12-02T08:59:59+00:00"
    assert wf.expire_due(before) == 0
    assert wf.is_exempted("RULE-1", "asset-1", now=before) is True
    assert wf.expire_due(DEFAULT_EXPIRY) == 1
    assert wf.get_exemption("EX-1").state == "expired"
    assert wf.is_exempted("RULE-1", "asset-1", now=DEFAULT_EXPIRY) is False
    assert wf.expire_due(DEFAULT_EXPIRY) == 0
    assert wf.stats()["expired"] == 1
    assert [row.exemption_id for row in wf.list_by_state("expired")] == ["EX-1"]


def test_renew_creates_new_version_and_supersedes_old():
    wf = workflow()
    active(wf)
    second = wf.renew_exemption("EX-1", 3600, "carol",
                                now="2026-11-25T10:00:00+00:00")
    assert second.version == 2
    assert second.state == "active"
    assert second.expires_at == "2026-11-25T11:00:00+00:00"
    assert second.approved_by == "carol"
    rows = wf.versions("EX-1")
    assert [row.state for row in rows] == ["superseded", "active"]
    assert rows[0].expires_at == DEFAULT_EXPIRY
    assert rows[0].superseded_by == 2
    third = wf.renew_exemption("EX-1", 1800, "dave",
                               now="2026-11-25T10:30:00+00:00")
    assert third.version == 3
    assert third.expires_at == "2026-11-25T11:00:00+00:00"
    rows = wf.versions("EX-1")
    assert [row.version for row in rows] == [1, 2, 3]
    assert [row.state for row in rows] == ["superseded", "superseded", "active"]
    assert rows[1].superseded_by == 3
    assert wf.stats()["renewed"] == 2
    tail = wf.history("EX-1")[-2:]
    assert [(event["version"], event["from"], event["to"]) for event in tail] == [
        (2, "active", "superseded"),
        (3, None, "active"),
    ]


def test_live_capacity_counts_only_non_terminal_exemptions():
    wf = workflow(max_live_exemptions=2)
    request(wf, "EX-1")
    request(wf, "EX-2")
    with pytest.raises(vw.ExemptionError) as exc:
        request(wf, "EX-3")
    assert exc.value.code == "EXEMPTION_CAPACITY_EXCEEDED"
    wf.revoke_exemption("EX-1", "no longer needed", now=NOW)
    request(wf, "EX-3")
    assert sorted(row.exemption_id for row in wf.list_by_state("requested")) == [
        "EX-2", "EX-3"]


def test_snapshot_is_detached_and_restore_roundtrips():
    wf = workflow()
    wf.register_asset("asset-1", ["pci", "prod"], now=NOW)
    active(wf)
    state = wf.snapshot()
    assert set(state) == {"version", "exemptions", "assets", "history", "counters"}
    pristine = copy.deepcopy(state)
    state["counters"]["requested"] = 999
    state["history"].clear()
    assert wf.stats()["requested"] == 1
    assert len(wf.history("EX-1")) == 3

    clone = workflow()
    clone.restore(pristine)
    assert clone.stats() == wf.stats()
    assert clone.versions("EX-1") == wf.versions("EX-1")
    assert clone.get_exemption("EX-1") == wf.get_exemption("EX-1")
    assert clone.is_exempted("RULE-1", "asset-1", now=NOW) is True


def test_restore_rejects_malformed_state_without_touching_live_data():
    wf = workflow()
    request(wf)
    good = wf.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "exemptions": good["exemptions"], "assets": good["assets"],
         "history": good["history"], "counters": good["counters"]},
        {"version": 1, "exemptions": [], "assets": good["assets"],
         "history": good["history"], "counters": good["counters"]},
        {"version": 1, "exemptions": good["exemptions"], "assets": [],
         "history": good["history"], "counters": good["counters"]},
        {"version": 1, "exemptions": good["exemptions"], "assets": good["assets"],
         "history": [], "counters": good["counters"]},
        {"version": 1, "exemptions": good["exemptions"], "assets": good["assets"],
         "history": good["history"], "counters": {"requested": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(vw.ExemptionError) as exc:
            wf.restore(bad)
        assert exc.value.code == "EXEMPTION_SNAPSHOT_INVALID"
    assert wf.stats()["requested"] == 1
    assert wf.get_exemption("EX-1").state == "requested"
    assert len(wf.history("EX-1")) == 1


def test_concurrent_requests_are_atomic_and_isolated():
    wf = workflow()
    errors = []
    barrier = threading.Barrier(20)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            wf.register_asset(f"asset-{index:02d}", ["pci"], now=NOW)
            wf.request_exemption(f"EX-{index:02d}", "RULE-1", ["pci"],
                                 f"user-{index:02d}", now=NOW)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    rows = wf.list_by_state("requested")
    assert len(rows) == 20
    assert sorted(row.exemption_id for row in rows) == [
        f"EX-{index:02d}" for index in range(20)]
    assert all(row.version == 1 for row in rows)
    assert wf.stats()["requested"] == 20
