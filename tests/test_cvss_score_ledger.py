import hashlib
import hmac
import json

import pytest

import cvss_score_ledger as cl

SECRET = "s" * 32
BG_SECRET = "g" * 32

V_HIGH = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
V_XSS = "CVSS:3.1/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N"
V_INFO = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign(secret, prefix, obj):
    payload = prefix.encode("utf-8") + canon(obj)
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def table():
    return {
        "AV": {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2},
        "AC": {"L": 0.77, "H": 0.44},
        "PR_U": {"N": 0.85, "L": 0.62, "H": 0.27},
        "PR_C": {"N": 0.85, "L": 0.68, "H": 0.5},
        "UI": {"N": 0.85, "R": 0.62},
        "C": {"H": 0.56, "L": 0.22, "N": 0.0},
        "I": {"H": 0.56, "L": 0.22, "N": 0.0},
        "A": {"H": 0.56, "L": 0.22, "N": 0.0},
    }


def ledger(**kwargs):
    g = cl.CvssScoreLedger(**kwargs)
    g.register_key("main", SECRET)
    g.install_table(table(), key_id="main", signature=sign(SECRET, "table:", table()))
    return g


def bundle(g, cve, vector, score, revised, key="main", secret=SECRET, **kwargs):
    body = {"cve": cve, "vector": vector, "score": score, "revised": revised}
    return g.ingest(cve, vector, score, revised=revised, key_id=key,
                    signature=sign(secret, "bundle:", body), **kwargs)


def test_compute_score_matches_published_examples():
    g = ledger()
    assert g.compute_score(V_HIGH) == 9.8
    assert g.compute_score(V_XSS) == 6.1
    assert g.compute_score(V_INFO) == 7.5


def test_compute_score_handles_scope_change_and_zero_impact():
    g = ledger()
    assert g.compute_score("CVSS:3.1/AV:N/AC:L/PR:L/UI:N/S:C/C:H/I:H/A:H") == 9.9
    assert g.compute_score("CVSS:3.1/AV:N/AC:L/PR:H/UI:N/S:U/C:H/I:H/A:H") == 7.2
    assert g.compute_score("CVSS:3.1/AV:P/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:N") == 0.0


def test_vector_validation_rejects_malformed_input():
    g = ledger()
    bad_vectors = [
        "AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H",
        "CVSS:3.1/AV:N/AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1/AV:X/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
        "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H/Z:Q",
    ]
    for vector in bad_vectors:
        with pytest.raises(cl.CvssScoreError) as exc:
            g.compute_score(vector)
        assert exc.value.code == "CVSS_VECTOR_INVALID"


def test_severity_bands_follow_the_published_thresholds():
    g = ledger()
    assert g.severity(0.0) == "None"
    assert g.severity(0.1) == "Low"
    assert g.severity(3.9) == "Low"
    assert g.severity(4.0) == "Medium"
    assert g.severity(6.9) == "Medium"
    assert g.severity(7.0) == "High"
    assert g.severity(8.9) == "High"
    assert g.severity(9.0) == "Critical"
    assert g.severity(10.0) == "Critical"


def test_register_key_validates_duplicates_and_capacity():
    g = cl.CvssScoreLedger(max_keys=2)
    with pytest.raises(cl.CvssScoreError) as exc:
        g.register_key("", SECRET)
    assert exc.value.code == "CVSS_KEY_INVALID"
    with pytest.raises(cl.CvssScoreError) as exc:
        g.register_key("short", "tiny")
    assert exc.value.code == "CVSS_KEY_INVALID"
    out = g.register_key("a", SECRET)
    assert out["key_id"] == "a"
    assert out["break_glass"] is False
    with pytest.raises(cl.CvssScoreError) as exc:
        g.register_key("a", SECRET)
    assert exc.value.code == "CVSS_KEY_EXISTS"
    g.register_key("b", BG_SECRET, break_glass=True)
    with pytest.raises(cl.CvssScoreError) as exc:
        g.register_key("c", "c" * 32)
    assert exc.value.code == "CVSS_CAPACITY_EXCEEDED"


def test_install_table_checks_shape_then_signature():
    g = cl.CvssScoreLedger()
    g.register_key("main", SECRET)
    incomplete = table()
    del incomplete["AC"]
    with pytest.raises(cl.CvssScoreError) as exc:
        g.install_table(incomplete, key_id="main", signature="")
    assert exc.value.code == "CVSS_TABLE_INVALID"
    with pytest.raises(cl.CvssScoreError) as exc:
        g.install_table(table(), key_id="main", signature="deadbeef")
    assert exc.value.code == "CVSS_SIGNATURE_INVALID"
    out = g.install_table(table(), key_id="main", signature=sign(SECRET, "table:", table()))
    assert len(out["digest"]) == 64
    audit = g.audit_table()
    assert audit["ok"] is True
    assert audit["drifted"] == []


def test_ingest_publishes_a_signed_record():
    g = ledger()
    out = bundle(g, "CVE-2024-1234", V_HIGH, 9.8, 1)
    assert out["status"] == "published"
    record = g.get("CVE-2024-1234")
    assert record["cve"] == "CVE-2024-1234"
    assert record["score"] == 9.8
    assert record["severity"] == "Critical"
    assert record["revised"] == 1
    assert record["key_id"] == "main"
    assert record["vector"] == V_HIGH
    assert [item["revised"] for item in g.history("CVE-2024-1234")] == [1]
    assert g.stats()["published"] == 1


def test_ingest_rejects_tampered_signature_without_state():
    g = ledger()
    cve = "CVE-2024-2000"
    body = {"cve": cve, "vector": V_HIGH, "score": 9.8, "revised": 1}
    signature = sign(SECRET, "bundle:", body)
    with pytest.raises(cl.CvssScoreError) as exc:
        g.ingest(cve, V_HIGH, 9.9, revised=1, key_id="main", signature=signature)
    assert exc.value.code == "CVSS_SIGNATURE_INVALID"
    assert g.get(cve) is None
    assert g.history(cve) == []
    assert g.stats()["signature_failures"] == 1


def test_ingest_quarantines_a_score_that_does_not_recompute():
    g = ledger()
    cve = "CVE-2024-2100"
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, cve, V_XSS, 9.9, 1)
    assert exc.value.code == "CVSS_SCORE_MISMATCH"
    assert exc.value.detail["claimed"] == 9.9
    assert exc.value.detail["computed"] == 6.1
    assert g.get(cve) is None
    assert g.quarantined() == [cve]
    assert g.stats()["quarantined"] == 1


def test_revisions_must_increase_and_history_stays_ordered():
    g = ledger()
    cve = "CVE-2024-3000"
    bundle(g, cve, V_HIGH, 9.8, 1)
    bundle(g, cve, V_XSS, 6.1, 2)
    assert [item["revised"] for item in g.history(cve)] == [1, 2]
    assert g.get(cve)["revised"] == 2
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, cve, V_INFO, 7.5, 2)
    assert exc.value.code == "CVSS_REVISION_STALE"
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, cve, V_INFO, 7.5, 0)
    assert exc.value.code == "CVSS_REVISION_INVALID"
    assert g.get(cve)["revised"] == 2
    assert g.stats()["stale_revisions"] == 1


def test_duplicate_bundle_is_idempotent():
    g = ledger()
    cve = "CVE-2024-4000"
    body = {"cve": cve, "vector": V_HIGH, "score": 9.8, "revised": 1}
    signature = sign(SECRET, "bundle:", body)
    first = g.ingest(cve, V_HIGH, 9.8, revised=1, key_id="main", signature=signature)
    second = g.ingest(cve, V_HIGH, 9.8, revised=1, key_id="main", signature=signature)
    assert first["status"] == "published"
    assert second["status"] == "duplicate"
    assert g.stats()["duplicates"] == 1
    assert len(g.history(cve)) == 1


def test_entry_capacity_and_revision_trimming():
    g = ledger(max_entries=1, max_revisions=2)
    cve = "CVE-2024-5000"
    bundle(g, cve, V_HIGH, 9.8, 1)
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, "CVE-2024-5001", V_HIGH, 9.8, 1)
    assert exc.value.code == "CVSS_CAPACITY_EXCEEDED"
    assert g.get("CVE-2024-5001") is None
    bundle(g, cve, V_XSS, 6.1, 2)
    bundle(g, cve, V_INFO, 7.5, 3)
    assert [item["revised"] for item in g.history(cve)] == [2, 3]
    assert g.stats()["superseded"] == 1
    assert g.get(cve)["revised"] == 3


def test_weight_override_needs_break_glass_and_suspends_ingest():
    g = cl.CvssScoreLedger()
    g.register_key("main", SECRET)
    g.register_key("break", BG_SECRET, break_glass=True)
    g.install_table(table(), key_id="main", signature=sign(SECRET, "table:", table()))
    body = {"metric": "AV", "level": "N", "value": 0.9}
    with pytest.raises(cl.CvssScoreError) as exc:
        g.override_weight("AV", "N", 0.9, key_id="main",
                          signature=sign(SECRET, "override:", body))
    assert exc.value.code == "CVSS_OVERRIDE_DENIED"
    out = g.override_weight("AV", "N", 0.9, key_id="break",
                            signature=sign(BG_SECRET, "override:", body))
    assert out["drifted"] is True
    audit = g.audit_table()
    assert audit["ok"] is False
    assert audit["drifted"] == ["AV"]
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, "CVE-2024-6000", V_HIGH, 9.8, 1)
    assert exc.value.code == "CVSS_TABLE_DRIFT"
    assert g.stats()["overrides"] == 1
    g.install_table(table(), key_id="main", signature=sign(SECRET, "table:", table()))
    assert g.audit_table()["ok"] is True
    assert bundle(g, "CVE-2024-6000", V_HIGH, 9.8, 1)["status"] == "published"


def test_revoked_key_can_no_longer_sign_bundles():
    g = ledger()
    assert g.revoke_key("main") is True
    assert g.revoke_key("main") is False
    with pytest.raises(cl.CvssScoreError) as exc:
        bundle(g, "CVE-2024-7000", V_HIGH, 9.8, 1)
    assert exc.value.code == "CVSS_SIGNATURE_INVALID"
    assert g.stats()["signature_failures"] == 1
    assert g.get("CVE-2024-7000") is None


def test_snapshot_restore_roundtrip_and_rejection():
    g = ledger()
    bundle(g, "CVE-2024-8000", V_HIGH, 9.8, 1)
    snap = g.snapshot()
    bundle(g, "CVE-2024-8001", V_XSS, 6.1, 1)
    assert len(snap["entries"]) == 1
    g.restore(snap)
    assert g.get("CVE-2024-8001") is None
    assert g.get("CVE-2024-8000")["score"] == 9.8
    assert bundle(g, "CVE-2024-8001", V_XSS, 6.1, 1)["status"] == "published"
    with pytest.raises(cl.CvssScoreError) as exc:
        g.restore({"entries": []})
    assert exc.value.code == "CVSS_SNAPSHOT_INVALID"
    assert g.get("CVE-2024-8001") is not None
