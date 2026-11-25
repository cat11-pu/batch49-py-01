import threading

import pytest

import suppression_blast_radius_auditor as sba

NOW = "2026-11-25T12:00:00+00:00"
RECENT = "2026-11-20T00:00:00+00:00"


def rule(rule_id, *, pattern="src/*", expires=None, enabled=True):
    return {
        "rule_id": rule_id,
        "pattern": pattern,
        "created_at": "2026-01-01T00:00:00+00:00",
        "expires_at": expires,
        "enabled": enabled,
    }


def hit(hit_id, rule_id, path, *, batch="b1", at=RECENT, line=1):
    return {
        "hit_id": hit_id,
        "rule_id": rule_id,
        "path": path,
        "line": line,
        "batch_id": batch,
        "observed_at": at,
    }


def auditor():
    return sba.SuppressionBlastRadiusAuditor()


def test_load_rules_counts_and_stats():
    obj = auditor()
    loaded = obj.load_rules([rule("r1"), rule("r2"),
                             rule("r3", enabled=False)])
    assert loaded == 3
    listed = obj.rules()
    assert [r.rule_id for r in listed] == ["r1", "r2", "r3"]
    assert [r.enabled for r in listed] == [True, True, False]
    assert obj.stats() == {"rules": 3, "enabled": 2, "hits": 0,
                           "batches": 0, "config_version": 1, "audits": 0}


def test_load_rules_rejects_duplicate_ids():
    obj = auditor()
    with pytest.raises(sba.AuditError) as exc:
        obj.load_rules([rule("r1"), rule("r1")])
    assert exc.value.code == "AUDIT_RULE_DUPLICATE"
    assert obj.stats()["rules"] == 0
    assert obj.stats()["config_version"] == 0


def test_load_rules_rejects_invalid_rule_atomically():
    obj = auditor()
    with pytest.raises(sba.AuditError) as exc:
        obj.load_rules([rule("r1"), {"rule_id": "r2"}])
    assert exc.value.code == "AUDIT_RULE_INVALID"
    assert exc.value.detail == 1
    assert obj.stats()["rules"] == 0
    assert obj.stats()["config_version"] == 0


def test_record_hits_counts_batches():
    obj = auditor()
    obj.load_rules([rule("r1")])
    accepted = obj.record_hits([
        hit("h1", "r1", "src/a.py", batch="b1"),
        hit("h2", "r1", "src/b.py", batch="b2"),
        hit("h3", "r1", "src/c.py", batch="b1"),
    ])
    assert accepted == 3
    stats = obj.stats()
    assert stats["hits"] == 3
    assert stats["batches"] == 2


def test_record_hits_rejects_duplicates_and_unknown_rules():
    obj = auditor()
    obj.load_rules([rule("r1")])
    obj.record_hits([hit("h1", "r1", "src/a.py")])
    with pytest.raises(sba.AuditError) as exc:
        obj.record_hits([hit("h1", "r1", "src/a.py")])
    assert exc.value.code == "AUDIT_HIT_DUPLICATE"
    with pytest.raises(sba.AuditError) as exc:
        obj.record_hits([hit("h2", "ghost", "src/b.py")])
    assert exc.value.code == "AUDIT_UNKNOWN_RULE"
    assert obj.stats()["hits"] == 1


def test_coverage_uses_distinct_paths():
    obj = auditor()
    obj.load_rules([rule("r1"), rule("r2")])
    obj.record_hits([
        hit("h1", "r1", "src/a.py"),
        hit("h2", "r1", "src/b.py"),
        hit("h3", "r2", "src/c.py"),
    ])
    entries = obj.coverage(now=NOW)
    assert entries == (
        sba.CoverageEntry("r1", 2, 2, 0.667),
        sba.CoverageEntry("r2", 1, 1, 0.333),
    )


def test_coverage_excludes_disabled_rules():
    obj = auditor()
    obj.load_rules([rule("r1"), rule("r2"), rule("r3", enabled=False)])
    obj.record_hits([
        hit("h1", "r1", "src/a.py"),
        hit("h2", "r2", "src/b.py"),
        hit("h3", "r3", "src/c.py"),
    ])
    entries = obj.coverage(now=NOW)
    assert [entry.rule_id for entry in entries] == ["r1", "r2"]
    assert [entry.ratio for entry in entries] == [0.5, 0.5]


def test_audit_flags_overbroad_rule():
    obj = auditor()
    obj.load_rules([rule("r1"), rule("r2")])
    obj.record_hits([
        hit("h1", "r1", "src/a.py"),
        hit("h2", "r1", "src/b.py"),
        hit("h3", "r1", "src/c.py"),
        hit("h4", "r2", "src/d.py"),
    ])
    report = obj.audit(now=NOW)
    assert report.overbroad == ("r1",)
    assert report.distinct_paths == 4
    assert report.coverage[0] == sba.CoverageEntry("r1", 3, 3, 0.75)


def test_audit_lists_expired_and_cleanup():
    obj = auditor()
    obj.load_rules([rule("r1", expires="2026-11-01T00:00:00+00:00"),
                    rule("r2")])
    obj.record_hits([hit("h1", "r2", "src/a.py")])
    report = obj.audit(now=NOW)
    assert report.expired == ("r1",)
    assert report.cleanup == ("r1",)
    assert obj.cleanup(now=NOW) == ("r1",)


def test_coverage_honours_window():
    obj = auditor()
    obj.load_rules([rule("r1")])
    obj.record_hits([
        hit("h-old", "r1", "src/old.py", at="2026-10-01T00:00:00+00:00"),
        hit("h-new", "r1", "src/new.py", at=RECENT),
    ])
    entries = obj.coverage(now=NOW)
    assert entries == (sba.CoverageEntry("r1", 1, 1, 1.0),)
    report = obj.audit(now=NOW)
    assert report.distinct_paths == 1


def test_audit_metadata_and_history():
    obj = auditor()
    obj.load_rules([rule("r1"), rule("r2")])
    obj.load_rules([rule("r1"), rule("r2")])
    first = obj.audit(now=NOW)
    second = obj.audit(now=NOW)
    assert first.snapshot_at == NOW
    assert first.config_version == 2
    assert first.rules_total == 2
    assert first.enabled_total == 2
    assert first == second
    assert len(obj.audit_history()) == 2
    assert obj.audit_history()[0]["config_version"] == 2
    assert obj.stats()["audits"] == 2


def test_audit_does_not_mutate_configuration():
    obj = auditor()
    obj.load_rules([rule("r1")])
    obj.record_hits([hit("h1", "r1", "src/a.py")])
    before = obj.snapshot()
    obj.audit(now=NOW)
    after = obj.snapshot()
    assert before["rules"] == after["rules"]
    assert before["hits"] == after["hits"]
    assert before["config_version"] == after["config_version"]


def test_rule_detail_reports_counts():
    obj = auditor()
    obj.load_rules([rule("r1")])
    obj.record_hits([hit("h1", "r1", "src/a.py"),
                     hit("h2", "r1", "src/b.py")])
    detail = obj.rule_detail("r1", now=NOW)
    assert detail["rule_id"] == "r1"
    assert detail["distinct_hits"] == 2
    assert detail["distinct_paths"] == 2
    assert detail["ratio"] == 1.0
    with pytest.raises(sba.AuditError) as exc:
        obj.rule_detail("ghost", now=NOW)
    assert exc.value.code == "AUDIT_RULE_UNKNOWN"


def test_snapshot_restore_roundtrip():
    obj = auditor()
    obj.load_rules([rule("r1"), rule("r2")])
    obj.record_hits([hit("h1", "r1", "src/a.py"),
                     hit("h2", "r2", "src/b.py", batch="b2")])
    obj.audit(now=NOW)
    snap = obj.snapshot()
    clone = auditor()
    clone.restore(snap)
    assert clone.stats() == obj.stats()
    assert clone.coverage(now=NOW) == obj.coverage(now=NOW)
    assert clone.audit_history() == obj.audit_history()
    assert clone.audit(now=NOW) == obj.audit(now=NOW)
    stats_before = obj.stats()
    with pytest.raises(sba.AuditError) as exc:
        obj.restore({"rules": "invalid"})
    assert exc.value.code == "AUDIT_SNAPSHOT_INVALID"
    assert obj.stats() == stats_before


def test_concurrent_hit_recording_is_atomic():
    obj = auditor()
    obj.load_rules([rule("r0"), rule("r1"), rule("r2"), rule("r3")])
    errors = []
    totals = []

    def worker(index):
        try:
            count = obj.record_hits([
                hit("h-%d-%d" % (index, n), "r%d" % index,
                    "path-%d-%d.py" % (index, n), batch="b%d" % index)
                for n in range(25)
            ])
            totals.append(count)
        except Exception as err:  # pragma: no cover - surfaced by assertion
            errors.append(err)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(totals) == [25, 25, 25, 25]
    assert obj.stats()["hits"] == 100
    entries = obj.coverage(now=NOW)
    assert [entry.rule_id for entry in entries] == ["r0", "r1", "r2", "r3"]
    assert [entry.ratio for entry in entries] == [0.25, 0.25, 0.25, 0.25]
