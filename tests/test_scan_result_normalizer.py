import pytest

import scan_result_normalizer as srn

VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


def record(**over):
    base = {
        "source": "scanner-a",
        "vuln_id": "CVE-2026-4001",
        "title": "Reflected XSS",
        "path": "src/app.py:12",
        "severity": "high",
        "weakness": "CWE-79",
    }
    base.update(over)
    return base


def norm():
    return srn.ScanResultNormalizer()


def test_map_severity_known_words():
    table = {
        "critical": "critical", "crit": "critical", "blocker": "critical",
        "high": "high", "severe": "high", "major": "high",
        "medium": "medium", "moderate": "medium", "warning": "medium",
        "warn": "medium", "low": "low", "minor": "low",
        "info": "info", "informational": "info", "note": "info",
        "trivial": "info",
    }
    obj = norm()
    for word, level in table.items():
        mapped = obj.map_severity(word)
        assert mapped.level == level, word
        assert mapped.raw == word
        assert mapped.conservative is False


def test_map_severity_normalizes_case_and_space():
    obj = norm()
    mapped = obj.map_severity("  HIGH  ")
    assert mapped.level == "high"
    assert mapped.raw == "HIGH"
    assert mapped.conservative is False
    assert obj.map_severity("Moderate").level == "medium"


def test_map_severity_rejects_empty_and_non_string():
    obj = norm()
    for bad in ("", "   ", None, 7):
        with pytest.raises(srn.NormalizeError) as exc:
            obj.map_severity(bad)
        assert exc.value.code == "SEVERITY_INVALID"


def test_parse_cvss_valid_vector():
    obj = norm()
    parsed = obj.parse_cvss(VECTOR)
    assert parsed.parsed is True
    assert parsed.base_score == 5.3
    assert parsed.raw == VECTOR
    assert parsed.reason == ""
    assert parsed.components == (
        ("AC", "L"), ("AV", "N"), ("C", "H"), ("I", "H"),
        ("PR", "N"), ("S", "U"), ("UI", "N"),
    )
    assert dict(parsed.components)["S"] == "U"


def test_parse_cvss_absent_vector_stays_empty():
    obj = norm()
    for absent in (None, ""):
        parsed = obj.parse_cvss(absent)
        assert parsed.parsed is False
        assert parsed.base_score is None
        assert parsed.components == ()
        assert parsed.reason == "absent"
        assert parsed.raw == ""


def test_normalize_path_extracts_line_suffix():
    obj = norm()
    info = obj.normalize_path("src/app.py:42")
    assert info.path == "src/app.py"
    assert info.line == 42
    plain = obj.normalize_path("src/app.py")
    assert plain.path == "src/app.py"
    assert plain.line is None
    spaced = obj.normalize_path("  src/app.py:7  ")
    assert (spaced.path, spaced.line) == ("src/app.py", 7)


def test_normalize_path_rejects_empty_and_absolute():
    obj = norm()
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_path("   ")
    assert exc.value.code == "PATH_INVALID"
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_path(123)
    assert exc.value.code == "PATH_INVALID"
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_path("/etc/passwd")
    assert exc.value.code == "PATH_ABSOLUTE"


def test_normalize_record_canonical_shape_and_aliases():
    obj = norm()
    raw = record()
    del raw["title"], raw["path"], raw["severity"], raw["weakness"]
    raw.update({"name": "Alias title", "file": "lib/x.py:9",
                "level": "CRITICAL", "cwe": "CWE-89"})
    out = obj.normalize_record(raw)
    assert tuple(out) == (
        "source", "vuln_id", "title", "weakness", "severity",
        "severity_raw", "conservative", "path", "line",
        "cvss_score", "cvss_parsed",
    )
    assert out["title"] == "Alias title"
    assert out["weakness"] == "CWE-89"
    assert out["severity"] == "critical"
    assert out["severity_raw"] == "CRITICAL"
    assert out["conservative"] is False
    assert out["path"] == "lib/x.py"
    assert out["line"] == 9
    assert out["cvss_score"] is None
    assert out["cvss_parsed"] is False


def test_normalize_record_propagates_cvss_score():
    obj = norm()
    out = obj.normalize_record(record(vector=VECTOR))
    assert out["cvss_score"] == 5.3
    assert out["cvss_parsed"] is True
    bare = obj.normalize_record(record())
    assert bare["cvss_score"] is None
    assert bare["cvss_parsed"] is False


def test_normalize_record_rejects_missing_source():
    obj = norm()
    bad = record()
    bad.pop("source")
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_record(bad)
    assert exc.value.code == "NORMALIZE_MISSING_FIELD"
    assert exc.value.detail == "source"
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_record("not-a-record")
    assert exc.value.code == "NORMALIZE_RECORD_INVALID"


def test_batch_keeps_order_and_rejections_with_raw_copy():
    obj = norm()
    bad = record(vuln_id="CVE-2026-4002", title="Broken")
    bad.pop("severity")
    records = [record(vuln_id="CVE-2026-4001"),
               bad,
               record(vuln_id="CVE-2026-4003", path="src/c.py:1")]
    report = obj.normalize_batch(records, batch_id="b1")
    assert (report.accepted, report.rejected) == (2, 1)
    assert report.done is True
    assert report.cursor == 3
    assert report.remaining == 0
    assert [r["vuln_id"] for r in obj.normalized("b1")] == [
        "CVE-2026-4001", "CVE-2026-4003"]
    rejected = obj.rejections("b1")
    assert len(rejected) == 1
    assert rejected[0]["index"] == 1
    assert rejected[0]["code"] == "NORMALIZE_MISSING_FIELD"
    bad["title"] = "Mutated afterwards"
    assert rejected[0]["raw"]["title"] == "Broken"
    assert obj.rejections("b1")[0]["raw"]["title"] == "Broken"


def test_batch_limit_and_resume_continue_cursor():
    obj = norm()
    records = [record(vuln_id="CVE-2026-500%d" % i, path="src/f%d.py" % i,
                      severity="low") for i in range(4)]
    first = obj.normalize_batch(records, batch_id="b1", limit=2)
    assert (first.accepted, first.done, first.cursor, first.remaining) == (
        2, False, 2, 2)
    assert obj.batch_state("b1")["cursor"] == 2
    second = obj.resume("b1", records)
    assert (second.accepted, second.done, second.cursor, second.remaining) == (
        4, True, 4, 0)
    assert [r["vuln_id"] for r in obj.normalized("b1")] == [
        "CVE-2026-5000", "CVE-2026-5001",
        "CVE-2026-5002", "CVE-2026-5003"]


def test_resume_mismatch_and_unknown_batch():
    obj = norm()
    records = [record(vuln_id="CVE-2026-6001"),
               record(vuln_id="CVE-2026-6002"),
               record(vuln_id="CVE-2026-6003")]
    obj.normalize_batch(records, batch_id="b1", limit=1)
    with pytest.raises(srn.NormalizeError) as exc:
        obj.resume("b1", records[:2])
    assert exc.value.code == "BATCH_MISMATCH"
    assert obj.batch_state("b1")["cursor"] == 1
    with pytest.raises(srn.NormalizeError) as exc:
        obj.resume("missing", records)
    assert exc.value.code == "BATCH_UNKNOWN"


def test_completed_batch_cannot_be_restarted():
    obj = norm()
    records = [record(vuln_id="CVE-2026-7001", severity="low")]
    obj.normalize_batch(records, batch_id="b1")
    with pytest.raises(srn.NormalizeError) as exc:
        obj.normalize_batch(records, batch_id="b1")
    assert exc.value.code == "BATCH_ALREADY_COMPLETE"
    with pytest.raises(srn.NormalizeError) as exc:
        obj.resume("b1", records)
    assert exc.value.code == "BATCH_ALREADY_COMPLETE"


def test_snapshot_restore_roundtrip_for_batches():
    obj = norm()
    records = [record(vuln_id="CVE-2026-8001"),
               record(vuln_id="CVE-2026-8002", severity="low"),
               record(vuln_id="CVE-2026-8003", path="src/c.py:3")]
    obj.normalize_batch(records, batch_id="b1", limit=2)
    obj.normalize_batch(records, batch_id="b2")
    snap = obj.snapshot()
    clone = norm()
    clone.restore(snap)
    assert clone.stats() == obj.stats()
    assert clone.normalized("b1") == obj.normalized("b1")
    assert clone.normalized("b2") == obj.normalized("b2")
    assert clone.rejections("b2") == obj.rejections("b2")
    assert clone.checkpoint("b1") == obj.checkpoint("b1")
    snap["batches"]["b2"]["outputs"].clear()
    assert len(obj.normalized("b2")) == 3
    with pytest.raises(srn.NormalizeError) as exc:
        obj.restore({"batches": "invalid"})
    assert exc.value.code == "SNAPSHOT_INVALID"
    assert obj.stats() == clone.stats()
