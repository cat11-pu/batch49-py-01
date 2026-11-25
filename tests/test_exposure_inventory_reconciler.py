import copy
import threading

import pytest

import exposure_inventory_reconciler as ir

NOW = "2026-11-25T10:00:00+00:00"
NEAR = "2026-11-25T10:59:59+00:00"
AFTER_SILENCE = "2026-11-25T11:00:00+00:00"


def reconciler(**kwargs):
    return ir.ExposureInventoryReconciler(**kwargs)


def entry(asset_id, hostname=None, ip="10.0.0.1", service="https",
          owner=None, tags=()):
    return ir.InventoryEntry(
        asset_id=asset_id,
        hostname=hostname if hostname is not None else f"{asset_id}.corp",
        ip=ip,
        service=service,
        owner=owner,
        tags=tuple(tags),
    )


def test_first_reconcile_reports_every_entry_as_added():
    rec = reconciler()
    report = rec.reconcile([entry("a-2"), entry("a-1")], now=NOW)
    assert report.baseline_count == 0
    assert report.current_count == 2
    assert report.removed == ()
    assert report.changed == ()
    assert [row.asset_id for row in report.added] == ["a-1", "a-2"]
    first = report.added[0]
    assert first.change_type == "added"
    assert first.before is None
    assert first.after == {
        "asset_id": "a-1", "hostname": "a-1.corp", "ip": "10.0.0.1",
        "service": "https", "owner": None, "tags": (),
    }
    assert first.fields == ()
    assert [row.asset_id for row in rec.current_entries()] == ["a-1", "a-2"]
    assert rec.last_report() == report
    assert rec.stats()["added"] == 2
    assert rec.stats()["snapshots"] == 1


def test_identical_snapshot_produces_no_changes():
    rec = reconciler()
    entries = [entry("a-1"), entry("a-2")]
    first = rec.reconcile(entries, now=NOW)
    second = rec.reconcile(entries, now=NEAR)
    assert len(first.added) == 2
    assert second.added == ()
    assert second.removed == ()
    assert second.changed == ()
    assert second.baseline_count == second.current_count == 2
    assert rec.stats()["added"] == 2
    assert rec.stats()["snapshots"] == 2


def test_reconcile_splits_added_removed_and_changed():
    rec = reconciler()
    rec.reconcile([entry("a-1"), entry("a-2"), entry("a-3")], now=NOW)
    report = rec.reconcile(
        [entry("a-1"), entry("a-2", hostname="a-2-renamed.corp"), entry("a-4")],
        now=NEAR,
    )
    assert [row.asset_id for row in report.added] == ["a-4"]
    assert [row.asset_id for row in report.removed] == ["a-3"]
    assert [row.asset_id for row in report.changed] == ["a-2"]
    removed = report.removed[0]
    assert removed.before["hostname"] == "a-3.corp"
    assert removed.after is None
    changed = report.changed[0]
    assert changed.before["hostname"] == "a-2.corp"
    assert changed.after["hostname"] == "a-2-renamed.corp"
    assert changed.fields == ("hostname",)
    assert (report.baseline_count, report.current_count) == (3, 3)


def test_tags_are_compared_as_a_normalized_set():
    rec = reconciler()
    rec.reconcile([entry("a-1", tags=("b", "a"))], now=NOW)
    same = rec.reconcile([entry("a-1", tags=("a", "b", "b"))], now=NEAR)
    assert same.changed == ()
    changed = rec.reconcile(
        [entry("a-1", hostname="a-1-new.corp", service="ssh", tags=("a", "c"))],
        now=AFTER_SILENCE,
    )
    row = changed.changed[0]
    assert row.fields == ("hostname", "service", "tags")
    assert row.before["tags"] == ("a", "b")
    assert row.after["tags"] == ("a", "c")
    assert rec.current_entries()[0].tags == ("a", "c")


def test_new_entries_inherit_owner_rules_with_explicit_owner_winning():
    rec = reconciler()
    rec.add_owner_rule("tag", "payments", "team-pay")
    rec.add_owner_rule("hostname_suffix", "corp.example", "team-corp")
    report = rec.reconcile([
        entry("a-1", tags=("payments",)),
        entry("a-2", owner="team-x", tags=("payments",)),
        entry("a-3", hostname="db.corp.example"),
        entry("a-4", hostname="db.corp.example", tags=("payments",)),
    ], now=NOW)
    assert [(row.asset_id, row.after["owner"]) for row in report.added] == [
        ("a-1", "team-pay"),
        ("a-2", "team-x"),
        ("a-3", "team-corp"),
        ("a-4", "team-pay"),
    ]
    assert rec.owner_of("a-1") == "team-pay"
    assert rec.owner_of("a-2") == "team-x"
    assert rec.owner_of("a-3") == "team-corp"
    assert rec.stats()["owner_rules"] == 2


def test_service_owner_rule_requires_exact_match():
    rec = reconciler()
    rec.add_owner_rule("service", "ssh", "team-ssh")
    rec.reconcile([
        entry("a-1", service="ssh"),
        entry("a-2", service="ssh-proxy"),
    ], now=NOW)
    assert rec.owner_of("a-1") == "team-ssh"
    assert rec.owner_of("a-2") is None


def test_invalid_entries_and_rules_are_rejected_without_state_change():
    rec = reconciler()
    bad_entries = [
        [entry("a-1"), entry("a-1")],
        [entry("")],
        [entry("a-1", tags=("ok", 5))],
        ["not-an-entry"],
    ]
    for entries in bad_entries:
        with pytest.raises(ir.ReconcileError) as exc:
            rec.reconcile(entries, now=NOW)
        assert exc.value.code == "RECON_INVALID_SNAPSHOT"
    for match_type, pattern, owner in (
        ("cidr", "10.0.0.0/8", "team-x"),
        ("tag", "", "team-x"),
        ("tag", "prod", ""),
    ):
        with pytest.raises(ir.ReconcileError) as exc:
            rec.add_owner_rule(match_type, pattern, owner)
        assert exc.value.code == "RECON_INVALID_REQUEST"
    with pytest.raises(ir.ReconcileError) as exc:
        rec.owner_of("missing")
    assert exc.value.code == "RECON_UNKNOWN_ASSET"
    assert rec.stats()["snapshots"] == 0
    assert rec.current_entries() == []


def test_capacity_cap_rejects_oversized_snapshot():
    rec = reconciler(max_entries=2)
    rec.reconcile([entry("a-1"), entry("a-2")], now=NOW)
    with pytest.raises(ir.ReconcileError) as exc:
        rec.reconcile([entry("a-1"), entry("a-2"), entry("a-3")], now=NEAR)
    assert exc.value.code == "RECON_CAPACITY_EXCEEDED"
    assert [row.asset_id for row in rec.current_entries()] == ["a-1", "a-2"]
    assert rec.stats()["snapshots"] == 1


def test_acknowledge_returns_matched_count_and_is_idempotent():
    rec = reconciler()
    rec.reconcile([entry("a-1"), entry("a-2"), entry("a-3")], now=NOW)
    assert rec.acknowledge(["a-1", "a-3", "missing"], now=NOW) == 2
    assert rec.unacknowledged() == ("a-2",)
    assert rec.acknowledge(["a-1", "a-3"], now=NEAR) == 0
    assert rec.stats()["acknowledged"] == 2


def test_acknowledge_ignores_entries_missing_from_current_snapshot():
    rec = reconciler()
    rec.reconcile([entry("a-1"), entry("a-2")], now=NOW)
    report = rec.reconcile([entry("a-1")], now=NEAR)
    assert [row.asset_id for row in report.removed] == ["a-2"]
    assert rec.acknowledge(["a-2"], now=NEAR) == 0
    assert rec.unacknowledged() == ()
    assert rec.stats()["acknowledged"] == 0


def test_reminders_are_suppressed_until_silence_elapsed():
    rec = reconciler()
    rec.reconcile([entry("a-1"), entry("a-2")], now=NOW)
    assert rec.reminder_batch(NOW) == ("a-1", "a-2")
    assert rec.reminder_batch(NEAR) == ()
    assert rec.reminder_batch(AFTER_SILENCE) == ("a-1", "a-2")
    assert rec.stats()["reminders"] == 4


def test_new_change_invalidates_previous_acknowledgement():
    rec = reconciler()
    rec.reconcile([entry("a-1")], now=NOW)
    assert rec.acknowledge(["a-1"], now=NOW) == 1
    rec.reconcile([entry("a-1")], now=NEAR)
    assert rec.unacknowledged() == ()
    rec.reconcile([entry("a-1", hostname="a-1-new.corp")], now=AFTER_SILENCE)
    assert rec.unacknowledged() == ("a-1",)
    assert rec.reminder_batch(AFTER_SILENCE) == ("a-1",)
    assert rec.acknowledge(["a-1"], now=AFTER_SILENCE) == 1
    assert rec.unacknowledged() == ()
    assert rec.stats()["changed"] == 1
    assert rec.stats()["acknowledged"] == 2


def test_snapshot_is_detached_and_restore_roundtrips():
    rec = reconciler()
    rec.add_owner_rule("tag", "prod", "team-prod")
    rec.reconcile([entry("a-1", tags=("prod",)), entry("a-2")], now=NOW)
    rec.acknowledge(["a-1"], now=NOW)
    rec.reminder_batch(NOW)
    state = rec.snapshot()
    assert set(state) == {"version", "inventory", "owner_memory", "acknowledged",
                          "last_notified", "rules", "counters"}
    pristine = copy.deepcopy(state)
    state["inventory"].clear()
    state["counters"]["added"] = 999
    assert len(rec.current_entries()) == 2
    assert rec.stats()["added"] == 2

    clone = reconciler()
    clone.restore(pristine)
    assert clone.current_entries() == rec.current_entries()
    assert clone.unacknowledged() == rec.unacknowledged()
    assert clone.last_report() == rec.last_report()
    assert clone.owner_of("a-1") == "team-prod"
    assert clone.stats() == rec.stats()


def test_restore_rejects_malformed_state_without_touching_live_data():
    rec = reconciler()
    rec.reconcile([entry("a-1")], now=NOW)
    good = rec.snapshot()
    bad_states = [
        None,
        {},
        {"version": 2, "inventory": good["inventory"],
         "owner_memory": good["owner_memory"], "acknowledged": good["acknowledged"],
         "last_notified": good["last_notified"], "rules": good["rules"],
         "counters": good["counters"]},
        {"version": 1, "inventory": [], "owner_memory": good["owner_memory"],
         "acknowledged": good["acknowledged"], "last_notified": good["last_notified"],
         "rules": good["rules"], "counters": good["counters"]},
        {"version": 1, "inventory": good["inventory"], "owner_memory": [],
         "acknowledged": good["acknowledged"], "last_notified": good["last_notified"],
         "rules": good["rules"], "counters": good["counters"]},
        {"version": 1, "inventory": good["inventory"],
         "owner_memory": good["owner_memory"], "acknowledged": [],
         "last_notified": good["last_notified"], "rules": good["rules"],
         "counters": good["counters"]},
        {"version": 1, "inventory": good["inventory"],
         "owner_memory": good["owner_memory"], "acknowledged": good["acknowledged"],
         "last_notified": [], "rules": good["rules"], "counters": good["counters"]},
        {"version": 1, "inventory": good["inventory"],
         "owner_memory": good["owner_memory"], "acknowledged": good["acknowledged"],
         "last_notified": good["last_notified"], "rules": {}, "counters": good["counters"]},
        {"version": 1, "inventory": good["inventory"],
         "owner_memory": good["owner_memory"], "acknowledged": good["acknowledged"],
         "last_notified": good["last_notified"], "rules": good["rules"],
         "counters": {"added": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(ir.ReconcileError) as exc:
            rec.restore(bad)
        assert exc.value.code == "RECON_SNAPSHOT_INVALID"
    assert [row.asset_id for row in rec.current_entries()] == ["a-1"]
    assert rec.stats()["added"] == 1
    assert rec.stats()["snapshots"] == 1


def test_concurrent_reminder_claim_never_returns_same_entry_twice():
    rec = reconciler()
    ids = [f"a-{index:02d}" for index in range(20)]
    rec.reconcile([entry(asset_id) for asset_id in ids], now=NOW)
    errors = []
    results = [None] * 4
    barrier = threading.Barrier(4)

    def worker(slot):
        try:
            barrier.wait(timeout=5)
            results[slot] = rec.reminder_batch(NOW)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(slot,)) for slot in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    merged = [asset_id for batch in results for asset_id in batch]
    assert len(merged) == 20
    assert sorted(merged) == ids
    assert rec.stats()["reminders"] == 20
