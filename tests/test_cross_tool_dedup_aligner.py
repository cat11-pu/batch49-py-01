import pytest

import cross_tool_dedup_aligner as cda


def make(source, *, vuln="CVE-2026-1001", weakness="CWE-79",
         path="src/app.py", line=10, severity="high"):
    return {
        "source": source,
        "vuln_id": vuln,
        "weakness": weakness,
        "path": path,
        "line": line,
        "severity": severity,
    }


def test_ingest_returns_sorted_keys_and_stats():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("scan-1", [
        make("scanner-b", vuln="CVE-2026-0002", path="src/b.py", line=3),
        make("scanner-a", vuln="CVE-2026-0001", path="src/a.py", line=7),
    ])
    assert keys == (
        "scanner-a|CVE-2026-0001|src/a.py|7",
        "scanner-b|CVE-2026-0002|src/b.py|3",
    )
    alerts = reg.alerts_for("scan-1")
    assert [a.source for a in alerts] == ["scanner-a", "scanner-b"]
    assert alerts[0].line == 7
    assert alerts[0].weakness == "CWE-79"
    assert reg.stats() == {"alerts": 2, "scans": 1, "groups": 0,
                           "pending": 0, "decisions": 0}


def test_ingest_invalid_alert_rejects_whole_batch():
    reg = cda.CrossToolDedupAligner()
    reg.ingest("scan-1", [make("scanner-a")])
    bad = make("scanner-b")
    bad.pop("vuln_id")
    with pytest.raises(cda.AlignError) as exc:
        reg.ingest("scan-2", [make("scanner-a"), bad])
    assert exc.value.code == "ALERT_INVALID"
    assert exc.value.detail == 1
    assert reg.alerts_for("scan-2") == ()
    assert reg.stats()["scans"] == 1
    assert reg.stats()["alerts"] == 1


def test_compare_identical_fields_scores_full():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [make("scanner-a"), make("scanner-b")])
    score = reg.compare(keys[0], keys[1])
    assert score.confidence == 100
    assert score.eligible is True
    assert score.reasons == ("vuln_id", "weakness", "path", "line_exact")
    assert reg.compare(keys[1], keys[0]) == score


def test_compare_line_distance_two_scores_ninety_five():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [make("scanner-a", line=10),
                            make("scanner-b", line=12)])
    score = reg.compare(keys[0], keys[1])
    assert score.confidence == 95
    assert score.eligible is True
    assert score.reasons == ("vuln_id", "weakness", "path", "line_near")


def test_compare_only_vuln_id_scores_fifty_not_eligible():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [
        make("scanner-a"),
        make("scanner-b", weakness="CWE-89", path="other/place.py", line=900),
    ])
    score = reg.compare(keys[0], keys[1])
    assert score.confidence == 50
    assert score.eligible is False
    assert score.reasons == ("vuln_id",)


def test_compare_same_source_not_comparable():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [
        make("scanner-a", vuln="CVE-2026-0001", path="src/a.py"),
        make("scanner-a", vuln="CVE-2026-0002", path="src/b.py"),
    ])
    score = reg.compare(keys[0], keys[1])
    assert score.confidence == 0
    assert score.eligible is False
    assert score.reasons == ("same_source",)


def test_align_merges_high_confidence_pair():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [make("scanner-b"), make("scanner-a")])
    result = reg.align("s")
    assert len(result.groups) == 1
    group = result.groups[0]
    assert group.keys == tuple(sorted(keys))
    assert group.sources == ("scanner-a", "scanner-b")
    assert group.confidence == 100
    assert group.adjudicated is False
    assert result.pending == ()
    assert reg.stats()["groups"] == 1


def test_align_below_threshold_goes_pending():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [
        make("scanner-a"),
        make("scanner-b", weakness="CWE-89", path="other/place.py", line=900),
    ])
    result = reg.align("s")
    assert result.groups == ()
    assert len(result.pending) == 1
    pair = result.pending[0]
    assert (pair.left, pair.right) == tuple(sorted(keys))
    assert pair.confidence == 50
    assert pair.reason == "below_threshold"


def test_align_repeat_is_idempotent():
    reg = cda.CrossToolDedupAligner()
    reg.ingest("s", [make("scanner-a"), make("scanner-b")])
    first = reg.align("s")
    second = reg.align("s")
    assert first.groups == second.groups
    assert len(first.groups) == 1
    assert reg.stats()["groups"] == 1
    assert reg.stats()["pending"] == 0


def test_align_scans_are_isolated():
    reg = cda.CrossToolDedupAligner()
    reg.ingest("s1", [make("scanner-a", vuln="CVE-2026-2001"),
                      make("scanner-b", vuln="CVE-2026-2001")])
    reg.ingest("s2", [make("scanner-a", vuln="CVE-2026-2002"),
                      make("scanner-b", vuln="CVE-2026-2002",
                           weakness="CWE-89", path="other/x.py", line=700)])
    assert len(reg.align("s1").groups) == 1
    assert len(reg.align("s2").pending) == 1
    stats = reg.stats()
    assert stats == {"alerts": 4, "scans": 2, "groups": 1,
                     "pending": 1, "decisions": 0}


def test_adjudicate_same_merges_below_threshold_pair():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [
        make("scanner-a"),
        make("scanner-b", weakness="CWE-89", path="other/place.py", line=900),
    ])
    decision = reg.adjudicate(keys[0], keys[1], "same",
                              operator="alice", note="same sink")
    assert (decision.left, decision.right) == tuple(sorted(keys))
    assert decision.verdict == "same"
    assert decision.operator == "alice"
    assert decision.note == "same sink"
    assert decision.seq == 1
    result = reg.align("s")
    assert len(result.groups) == 1
    assert result.groups[0].confidence == 50
    assert result.groups[0].adjudicated is True
    assert result.pending == ()
    assert len(reg.decisions()) == 1


def test_adjudicate_rejects_invalid_verdict_and_operator():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [make("scanner-a"), make("scanner-b")])
    with pytest.raises(cda.AlignError) as exc:
        reg.adjudicate(keys[0], keys[1], "merge", operator="alice")
    assert exc.value.code == "DECISION_INVALID"
    with pytest.raises(cda.AlignError) as exc:
        reg.adjudicate(keys[0], keys[1], "same", operator="   ")
    assert exc.value.code == "OPERATOR_REQUIRED"
    assert reg.stats()["decisions"] == 0
    assert reg.decision_for(keys[0], keys[1]) is None


def test_adjudicate_unknown_pair_rejected():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s", [make("scanner-a")])
    with pytest.raises(cda.AlignError) as exc:
        reg.adjudicate(keys[0], "scanner-z|CVE-2026-9999|nope.py|1", "same",
                       operator="alice")
    assert exc.value.code == "ALERT_UNKNOWN"


def test_snapshot_restore_reproduces_state():
    reg = cda.CrossToolDedupAligner()
    keys = reg.ingest("s1", [make("scanner-a"), make("scanner-b", line=11)])
    reg.align("s1")
    reg.adjudicate(keys[0], keys[1], "same", operator="alice", note="confirmed")
    snap = reg.snapshot()
    clone = cda.CrossToolDedupAligner()
    clone.restore(snap)
    assert clone.stats() == reg.stats()
    assert clone.decisions() == reg.decisions()
    assert clone.align("s1") == reg.align("s1")
    snap["decisions"].append({"left": "x", "right": "y", "verdict": "same",
                              "operator": "mallory", "note": "", "seq": 99})
    assert len(reg.decisions()) == 1
    with pytest.raises(cda.AlignError) as exc:
        reg.restore({"alerts": "not-a-mapping"})
    assert exc.value.code == "SNAPSHOT_INVALID"
    assert reg.stats() == clone.stats()


def test_stats_exact_counts_across_scans():
    reg = cda.CrossToolDedupAligner()
    reg.ingest("s1", [make("scanner-a", vuln="CVE-2026-3001"),
                      make("scanner-b", vuln="CVE-2026-3001")])
    reg.ingest("s2", [make("scanner-a", vuln="CVE-2026-3002"),
                      make("scanner-b", vuln="CVE-2026-3002",
                           weakness="CWE-89", path="other/place.py", line=900)])
    reg.align("s1")
    reg.align("s2")
    assert reg.stats() == {"alerts": 4, "scans": 2, "groups": 1,
                           "pending": 1, "decisions": 0}
