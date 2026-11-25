import copy

import pytest

import fim_drift_ledger as fim


def ledger(**kwargs):
    return fim.FimDriftLedger(**kwargs)


def base(reg, path="/etc/app.conf", *, digest="d1", size=10,
         mode=0o644, owner="root", inode=7):
    return reg.set_baseline(path, digest=digest, size=size, mode=mode,
                            owner=owner, inode=inode)


def obs(reg, path="/etc/app.conf", *, digest="d2", size=10,
        mode=0o644, owner="root", inode=7, now_ns=0):
    return reg.observe(path, digest=digest, size=size, mode=mode,
                       owner=owner, inode=inode, now_ns=now_ns)


def test_set_baseline_normalizes_path_and_returns_version():
    reg = ledger()
    assert base(reg, "/var//log/./app.log", digest="sha-1", size=32) == 1
    entry = reg.baseline_for("/var/log/app.log")
    assert isinstance(entry, fim.BaselineEntry)
    assert entry.path == "/var/log/app.log"
    assert entry.digest == "sha-1"
    assert entry.size == 32
    assert entry.mode == 0o644
    assert entry.owner == "root"
    assert entry.inode == 1
    assert entry.version == 1
    assert reg.baseline_for("/var/log/missing.log") is None
    assert reg.stats()["baselines"] == 1


def test_set_baseline_is_idempotent_and_bumps_on_change():
    reg = ledger()
    assert base(reg) == 1
    assert base(reg) == 1
    assert base(reg, digest="d9") == 2
    entry = reg.baseline_for("/etc/app.conf")
    assert entry.digest == "d9"
    assert entry.version == 2


def test_invalid_paths_and_records_are_rejected():
    reg = ledger()
    for path in ("", "relative/x", "/a/../b", "/a/./../b", "/a\x00b", 5):
        with pytest.raises(fim.FimDriftError) as exc:
            base(reg, path)
        assert exc.value.code == "FIM_INVALID_PATH"

    bad_records = [
        dict(digest="", size=1),
        dict(digest="d1", size=-1),
        dict(digest="d1", size=1, mode=0o10000),
        dict(digest="d1", size=1, owner="   "),
        dict(digest="d1", size=1, inode=-1),
    ]
    for kwargs in bad_records:
        with pytest.raises(fim.FimDriftError) as exc:
            reg.set_baseline("/etc/app.conf", **kwargs)
        assert exc.value.code == "FIM_INVALID_RECORD"
    assert reg.stats()["baselines"] == 0


def test_observe_reports_modified_fields_and_sequence():
    reg = ledger()
    base(reg)
    assert obs(reg, digest="d2", mode=0o600, now_ns=42) == 1
    drifts = reg.compare()
    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.path == "/etc/app.conf"
    assert drift.kind == "modified"
    assert drift.changed == ("digest", "mode")
    assert drift.baseline_version == 1
    assert drift.observed_digest == "d2"
    assert drift.seq == 1
    assert reg.stats()["observations"] == 1


def test_compare_reports_added_and_removed_in_path_order():
    reg = ledger()
    base(reg, "/a", digest="a1", size=1)
    base(reg, "/c", digest="c1", size=1)
    obs(reg, "/b", digest="b1", size=1)
    drifts = reg.compare()
    assert [(item.path, item.kind) for item in drifts] == [
        ("/a", "removed"), ("/b", "added"), ("/c", "removed")]
    assert drifts[1].baseline_version == 0
    assert drifts[1].observed_digest == "b1"
    assert drifts[1].seq == 1
    assert drifts[0].seq == 0


def test_compare_returns_empty_when_observation_matches_baseline():
    reg = ledger()
    base(reg)
    obs(reg, digest="d1")
    assert reg.compare() == ()
    assert reg.stats()["open_drifts"] == 0


def test_compare_since_filters_by_observation_sequence():
    reg = ledger()
    base(reg, "/a", digest="a1", size=1)
    base(reg, "/b", digest="b1", size=1)
    assert obs(reg, "/a", digest="a2", size=1) == 1
    assert obs(reg, "/b", digest="b2", size=1) == 2
    assert [item.path for item in reg.compare(since=1)] == ["/b"]
    assert [item.path for item in reg.compare(since=0)] == ["/a", "/b"]
    assert [item.path for item in reg.compare()] == ["/a", "/b"]


def test_quarantine_requires_drift_and_keeps_evidence():
    reg = ledger()
    base(reg)
    with pytest.raises(fim.FimDriftError) as exc:
        reg.quarantine("/etc/app.conf")
    assert exc.value.code == "FIM_NO_DRIFT"

    obs(reg, digest="d2")
    ticket = reg.quarantine("/etc/app.conf", now_ns=99)
    assert ticket.ticket_id == "q-1"
    assert ticket.path == "/etc/app.conf"
    assert ticket.drift_kind == "modified"
    assert ticket.baseline_version == 1
    assert ticket.observed_digest == "d2"
    assert ticket.sealed_at == 99
    assert [item.ticket_id for item in reg.tickets()] == ["q-1"]
    assert [item.kind for item in reg.compare()] == ["modified"]
    assert reg.baseline_for("/etc/app.conf").version == 1


def test_quarantine_duplicate_and_capacity():
    reg = ledger(max_open_drift=1)
    base(reg, "/a", digest="a1", size=1)
    base(reg, "/b", digest="b1", size=1)
    obs(reg, "/a", digest="a2", size=1)
    obs(reg, "/b", digest="b2", size=1)
    assert reg.quarantine("/a").ticket_id == "q-1"
    with pytest.raises(fim.FimDriftError) as exc:
        reg.quarantine("/a")
    assert exc.value.code == "FIM_ALREADY_QUARANTINED"
    with pytest.raises(fim.FimDriftError) as exc:
        reg.quarantine("/b")
    assert exc.value.code == "FIM_DRIFT_CAPACITY"
    assert [item.ticket_id for item in reg.tickets()] == ["q-1"]


def test_restore_writes_baseline_back_and_closes_ticket():
    reg = ledger()
    base(reg)
    obs(reg, digest="d2")
    reg.quarantine("/etc/app.conf")
    assert reg.restore("q-1") is True
    assert reg.compare() == ()
    assert reg.tickets() == ()
    assert reg.baseline_for("/etc/app.conf").version == 1
    assert reg.stats()["restored"] == 1
    with pytest.raises(fim.FimDriftError) as exc:
        reg.restore("q-1")
    assert exc.value.code == "FIM_TICKET_CLOSED"


def test_restore_unknown_ticket_and_added_path_without_baseline():
    reg = ledger()
    with pytest.raises(fim.FimDriftError) as exc:
        reg.restore("q-9")
    assert exc.value.code == "FIM_TICKET_UNKNOWN"

    obs(reg, "/new/file", digest="n1", size=3)
    assert reg.quarantine("/new/file").ticket_id == "q-1"
    with pytest.raises(fim.FimDriftError) as exc:
        reg.restore("q-1")
    assert exc.value.code == "FIM_NO_BASELINE"


def test_accept_promotes_observation_to_new_baseline():
    reg = ledger()
    base(reg)
    obs(reg, digest="d2", mode=0o600)
    reg.quarantine("/etc/app.conf")
    assert reg.accept("q-1") == 2
    entry = reg.baseline_for("/etc/app.conf")
    assert entry.digest == "d2"
    assert entry.mode == 0o600
    assert entry.version == 2
    assert reg.compare() == ()
    assert reg.tickets() == ()
    assert reg.stats()["accepted"] == 1


def test_accept_added_drift_creates_first_baseline():
    reg = ledger()
    obs(reg, "/new/file", digest="n1", size=3, mode=0o600)
    ticket = reg.quarantine("/new/file")
    assert ticket.baseline_version == 0
    assert reg.accept(ticket.ticket_id) == 1

    entry = reg.baseline_for("/new/file")
    assert entry.version == 1
    assert entry.digest == "n1"
    assert entry.mode == 0o600
    assert reg.compare() == ()
    assert reg.tickets() == ()
    assert reg.stats()["accepted"] == 1


def test_path_capacity_is_enforced_for_baseline_and_observation():
    reg = ledger(max_paths=2)
    base(reg, "/a", digest="a1", size=1)
    base(reg, "/b", digest="b1", size=1)
    with pytest.raises(fim.FimDriftError) as exc:
        base(reg, "/c", digest="c1", size=1)
    assert exc.value.code == "FIM_CAPACITY_EXCEEDED"
    with pytest.raises(fim.FimDriftError) as exc:
        obs(reg, "/c", digest="c1", size=1)
    assert exc.value.code == "FIM_CAPACITY_EXCEEDED"
    assert reg.baseline_for("/c") is None
    assert reg.stats()["baselines"] == 2


def test_snapshot_restore_state_roundtrip_and_invalid_payload():
    reg = ledger()
    base(reg, "/a", digest="a1", size=1)
    obs(reg, "/a", digest="a2", size=1)
    base(reg, "/b", digest="b1", size=1)
    reg.quarantine("/a")
    state = reg.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "baselines", "observations",
                             "quarantined", "closed_tickets", "counters",
                             "stats"}

    obs(reg, "/b", digest="b2", size=1)
    state["baselines"].clear()
    state["stats"]["observations"] = 99
    assert reg.stats()["observations"] == 2

    clone = ledger()
    clone.restore_state(pristine)
    assert clone.stats()["observations"] == 1
    assert clone.stats()["open_tickets"] == 1
    assert [item.path for item in clone.compare()] == ["/a", "/b"]
    assert [item.ticket_id for item in clone.tickets()] == ["q-1"]
    assert clone.baseline_for("/a").digest == "a1"

    for bad in (None, {}, {"version": 2}, {"version": 1, "baselines": []},
                {"version": 1, "baselines": {}, "observations": "bad",
                 "quarantined": {}, "closed_tickets": [], "counters": {},
                 "stats": {}}):
        with pytest.raises(fim.FimDriftError) as exc:
            clone.restore_state(bad)
        assert exc.value.code == "FIM_SNAPSHOT_INVALID"
    assert clone.stats()["observations"] == 1
