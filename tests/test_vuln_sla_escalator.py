import copy
import threading

import pytest

import vuln_sla_escalator as vs

START = "2026-11-01T00:00:00+00:00"
DUE = "2026-11-02T00:00:00+00:00"
DUE_PLUS_1 = "2026-11-03T00:00:00+00:00"
DUE_PLUS_2 = "2026-11-04T00:00:00+00:00"


def escalator(**kwargs):
    return vs.VulnSlaEscalator(**kwargs)


def open_vuln(esc, vuln_id="V-1", asset_id="asset-1", severity="critical", at=START):
    return esc.open_vulnerability(vuln_id, asset_id, severity, at)


def test_open_assigns_default_sla_per_severity():
    esc = escalator()
    critical = open_vuln(esc, "V-1", "asset-1", "critical")
    high = open_vuln(esc, "V-2", "asset-2", "high", START)
    assert critical.due_at == DUE
    assert high.due_at == "2026-11-04T00:00:00+00:00"
    assert critical.status == "open"
    assert critical.escalation_level == 0
    assert critical.freeze_seconds == 0
    assert critical.exemption_until is None
    assert critical.confirmed_at == START
    assert esc.stats()["opened"] == 2


def test_open_same_payload_is_idempotent_but_conflict_is_rejected():
    esc = escalator()
    first = open_vuln(esc)
    second = open_vuln(esc)
    assert second.due_at == first.due_at
    assert second.vuln_id == "V-1"
    assert esc.stats()["opened"] == 1
    with pytest.raises(vs.SlaError) as exc:
        open_vuln(esc, "V-1", "asset-2", "critical")
    assert exc.value.code == "SLA_INVALID_TRANSITION"
    assert esc.stats()["opened"] == 1


def test_open_rejects_invalid_payload_without_recording():
    esc = escalator()
    bad_calls = [
        ("", "asset-1", "critical", START),
        ("V-1", "", "critical", START),
        ("V-1", "asset-1", "urgent", START),
        ("V-1", "asset-1", "critical", "not-a-time"),
    ]
    for args in bad_calls:
        with pytest.raises(vs.SlaError) as exc:
            esc.open_vulnerability(*args)
        assert exc.value.code == "SLA_INVALID_REQUEST"
    assert esc.stats()["opened"] == 0
    assert esc.snapshot()["vulnerabilities"] == {}


def test_single_freeze_window_extends_due_and_counts_seconds():
    esc = escalator()
    open_vuln(esc)
    count = esc.add_freeze_window(
        "asset-1", "2026-11-01T02:00:00+00:00", "2026-11-01T06:00:00+00:00"
    )
    assert count == 1
    record = esc.get_vulnerability("V-1")
    assert record.due_at == "2026-11-02T04:00:00+00:00"
    assert record.freeze_seconds == 14400
    assert esc.stats()["freeze_windows"] == 1


def test_freeze_window_validation_and_per_asset_counts():
    esc = escalator()
    open_vuln(esc, "V-1", "asset-1")
    open_vuln(esc, "V-2", "asset-2")
    with pytest.raises(vs.SlaError) as exc:
        esc.add_freeze_window(
            "asset-1", "2026-11-01T06:00:00+00:00", "2026-11-01T06:00:00+00:00"
        )
    assert exc.value.code == "SLA_INVALID_REQUEST"
    assert esc.add_freeze_window(
        "asset-1", "2026-11-01T02:00:00+00:00", "2026-11-01T03:00:00+00:00"
    ) == 1
    assert esc.add_freeze_window(
        "asset-1", "2026-11-01T04:00:00+00:00", "2026-11-01T05:00:00+00:00"
    ) == 2
    assert esc.add_freeze_window(
        "asset-2", "2026-11-01T02:00:00+00:00", "2026-11-01T03:00:00+00:00"
    ) == 1
    assert esc.stats()["freeze_windows"] == 3


def test_escalation_advances_one_level_at_each_threshold():
    esc = escalator()
    open_vuln(esc)
    assert esc.evaluate("2026-11-01T23:59:59+00:00") == []
    first = esc.evaluate(DUE)
    assert [(event.level, event.at) for event in first] == [(1, DUE)]
    second = esc.evaluate(DUE_PLUS_1)
    assert [(event.level, event.at) for event in second] == [(2, DUE_PLUS_1)]
    third = esc.evaluate(DUE_PLUS_2)
    assert [(event.level, event.at) for event in third] == [(3, DUE_PLUS_2)]
    assert esc.evaluate("2026-11-05T00:00:00+00:00") == []
    assert esc.get_vulnerability("V-1").escalation_level == 3


def test_evaluate_catches_up_and_history_is_threshold_ordered():
    esc = escalator()
    open_vuln(esc)
    events = esc.evaluate(DUE_PLUS_2)
    assert [event.level for event in events] == [1, 2, 3]
    assert [event.at for event in events] == [DUE, DUE_PLUS_1, DUE_PLUS_2]
    assert [event.due_at for event in events] == [DUE, DUE, DUE]
    assert esc.escalation_history("V-1") == events
    assert esc.stats()["escalations"] == 3


def test_closed_vulnerability_is_skipped_by_evaluate_and_reopens():
    esc = escalator()
    open_vuln(esc)
    closed = esc.close_vulnerability("V-1", "patched", "2026-11-01T12:00:00+00:00")
    assert closed.status == "closed"
    assert esc.evaluate(DUE_PLUS_2) == []
    assert esc.escalation_history("V-1") == []
    reopened = esc.reopen_vulnerability("V-1", "2026-11-04T01:00:00+00:00")
    assert reopened.status == "open"
    assert esc.stats()["closed"] == 1
    assert esc.stats()["reopened"] == 1


def test_close_and_reopen_reject_invalid_transitions():
    esc = escalator()
    open_vuln(esc)
    with pytest.raises(vs.SlaError) as exc:
        esc.close_vulnerability("V-1", "", "2026-11-01T01:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_REQUEST"
    esc.close_vulnerability("V-1", "patched", "2026-11-01T02:00:00+00:00")
    with pytest.raises(vs.SlaError) as exc:
        esc.close_vulnerability("V-1", "patched-again", "2026-11-01T03:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_TRANSITION"
    esc.reopen_vulnerability("V-1", "2026-11-01T04:00:00+00:00")
    with pytest.raises(vs.SlaError) as exc:
        esc.reopen_vulnerability("V-1", "2026-11-01T05:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_TRANSITION"
    assert esc.stats()["closed"] == 1
    assert esc.stats()["reopened"] == 1


def test_unknown_vulnerability_uses_single_error_code():
    esc = escalator()
    calls = [
        lambda: esc.get_vulnerability("missing"),
        lambda: esc.escalation_history("missing"),
        lambda: esc.grant_exemption("missing", "2026-12-01T00:00:00+00:00", START),
        lambda: esc.close_vulnerability("missing", "patched", START),
        lambda: esc.reopen_vulnerability("missing", START),
    ]
    for call in calls:
        with pytest.raises(vs.SlaError) as exc:
            call()
        assert exc.value.code == "SLA_UNKNOWN_VULNERABILITY"


def test_exemption_grant_records_window_and_rejects_repeats():
    esc = escalator()
    open_vuln(esc)
    until = "2026-11-04T00:00:00+00:00"
    exemption = esc.grant_exemption("V-1", until, "2026-11-01T12:00:00+00:00")
    assert exemption.vuln_id == "V-1"
    assert exemption.until == until
    assert exemption.granted_at == "2026-11-01T12:00:00+00:00"
    assert exemption.revoked_at is None
    assert esc.get_vulnerability("V-1").exemption_until == until
    assert esc.stats()["exemptions"] == 1
    with pytest.raises(vs.SlaError) as exc:
        esc.grant_exemption("V-1", until, "2026-11-01T13:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_TRANSITION"
    with pytest.raises(vs.SlaError) as exc:
        esc.grant_exemption("V-1", "2026-11-01T00:00:00+00:00",
                            "2026-11-01T14:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_REQUEST"
    open_vuln(esc, "V-2", "asset-2")
    esc.close_vulnerability("V-2", "patched", "2026-11-01T15:00:00+00:00")
    with pytest.raises(vs.SlaError) as exc:
        esc.grant_exemption("V-2", until, "2026-11-01T16:00:00+00:00")
    assert exc.value.code == "SLA_INVALID_TRANSITION"


def test_capacity_cap_blocks_new_vulnerability():
    esc = escalator(max_vulnerabilities=2)
    open_vuln(esc, "V-1", "asset-1")
    open_vuln(esc, "V-2", "asset-2")
    with pytest.raises(vs.SlaError) as exc:
        open_vuln(esc, "V-3", "asset-3")
    assert exc.value.code == "SLA_CAPACITY_EXCEEDED"
    assert esc.stats()["opened"] == 2


def test_snapshot_is_detached_and_restore_roundtrips():
    esc = escalator()
    open_vuln(esc)
    esc.add_freeze_window(
        "asset-1", "2026-11-01T02:00:00+00:00", "2026-11-01T06:00:00+00:00"
    )
    esc.evaluate("2026-11-02T04:00:00+00:00")
    state = esc.snapshot()
    assert set(state) == {"version", "vulnerabilities", "freeze_windows",
                          "exemptions", "events", "counters"}
    pristine = copy.deepcopy(state)
    state["events"].clear()
    state["counters"]["opened"] = 999
    assert esc.stats()["opened"] == 1
    assert len(esc.escalation_history()) == 1

    clone = escalator()
    clone.restore(pristine)
    assert clone.stats() == esc.stats()
    assert clone.get_vulnerability("V-1") == esc.get_vulnerability("V-1")
    assert clone.escalation_history() == esc.escalation_history()


def test_restore_rejects_malformed_state_without_touching_live_data():
    esc = escalator()
    open_vuln(esc)
    good = esc.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "vulnerabilities": good["vulnerabilities"],
         "freeze_windows": good["freeze_windows"], "exemptions": good["exemptions"],
         "events": good["events"], "counters": good["counters"]},
        {"version": 1, "vulnerabilities": [], "freeze_windows": {},
         "exemptions": {}, "events": [], "counters": good["counters"]},
        {"version": 1, "vulnerabilities": good["vulnerabilities"],
         "freeze_windows": {}, "exemptions": [], "events": {},
         "counters": good["counters"]},
    ]
    for bad in bad_states:
        with pytest.raises(vs.SlaError) as exc:
            esc.restore(bad)
        assert exc.value.code == "SLA_SNAPSHOT_INVALID"
    assert esc.stats()["opened"] == 1
    assert esc.get_vulnerability("V-1").status == "open"
    assert esc.escalation_history() == []


def test_concurrent_open_and_evaluate_are_atomic():
    esc = escalator()
    errors = []
    barrier = threading.Barrier(12)

    def opener(index):
        try:
            barrier.wait(timeout=5)
            esc.open_vulnerability(
                f"V-{index:02d}", f"asset-{index:02d}", "critical", START
            )
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    openers = [threading.Thread(target=opener, args=(index,)) for index in range(12)]
    for thread in openers:
        thread.start()
    for thread in openers:
        thread.join()
    assert errors == []
    assert esc.stats()["opened"] == 12

    batches = []
    escalate_barrier = threading.Barrier(4)

    def evaluator():
        try:
            escalate_barrier.wait(timeout=5)
            batches.append(esc.evaluate(DUE_PLUS_2))
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    evaluators = [threading.Thread(target=evaluator) for _ in range(4)]
    for thread in evaluators:
        thread.start()
    for thread in evaluators:
        thread.join()
    assert errors == []
    events = [event for batch in batches for event in batch]
    assert len(events) == 36
    assert len({(event.vuln_id, event.level) for event in events}) == 36
    assert all(esc.get_vulnerability(f"V-{index:02d}").escalation_level == 3
               for index in range(12))
