import copy
import hashlib
import hmac
import json
import threading

import pytest

import audit_sig_chain as ac

GENESIS = "0" * 64


def chain(*, store=None, keys=None, active_key="k1", max_entries=4096,
          segment_size=8):
    return ac.AuditSignatureChain(store=store, keys=keys,
                                  active_key=active_key,
                                  max_entries=max_entries,
                                  segment_size=segment_size)


def canon(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def expected_sig(seq, key_id, actor, action, payload, now, prev, material):
    payload_digest = hashlib.sha256(canon(payload).encode()).hexdigest()
    message = f"{seq}\n{key_id}\n{actor}\n{action}\n{payload_digest}\n{now}\n{prev}"
    sig = hmac.new(material.encode(), message.encode(), hashlib.sha256).hexdigest()
    return payload_digest, sig


def test_first_entry_uses_genesis_and_exposes_fields():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    assert c.head() == {"seq": 0, "sig": GENESIS, "key_id": None}
    out = c.append("alice", "login", {"ip": "10.0.0.1"}, now=100)
    assert out["seq"] == 1
    assert out["key_id"] == "k1"
    assert out["actor"] == "alice"
    assert out["action"] == "login"
    assert out["payload"] == {"ip": "10.0.0.1"}
    assert out["now"] == 100
    assert out["prev_sig"] == GENESIS
    assert len(out["sig"]) == 64
    assert raw[0] == out
    assert c.entry(1) == out
    assert c.head() == {"seq": 1, "sig": out["sig"], "key_id": "k1"}


def test_signature_matches_documented_hmac_formula():
    c = chain(keys={"k1": "top-secret"})
    out = c.append("alice", "write", {"b": 2, "a": 1}, now=900)
    payload_digest, sig = expected_sig(1, "k1", "alice", "write",
                                       {"a": 1, "b": 2}, 900, GENESIS,
                                       "top-secret")
    assert out["payload_digest"] == payload_digest
    assert out["sig"] == sig


def test_payload_is_canonicalized_and_detached():
    first = chain(keys={"k1": "s1"})
    second = chain(keys={"k1": "s1"})
    payload = {"b": [1, 2], "a": True}
    row = first.append("alice", "read", payload, now=1)
    other = second.append("alice", "read", {"a": True, "b": [1, 2]}, now=1)
    assert row["payload_digest"] == other["payload_digest"]
    payload["a"] = False
    payload["b"].append(9)
    assert first.entry(1)["payload"] == {"a": True, "b": [1, 2]}


def test_sequence_is_contiguous_and_entry_lookup_is_bounded():
    c = chain(keys={"k1": "s1"})
    for index in range(3):
        assert c.append("svc", "tick", {"index": index}, now=index)["seq"] == index + 1
    assert c.head()["seq"] == 3
    assert [c.entry(seq)["seq"] for seq in (1, 2, 3)] == [1, 2, 3]
    for bad in (0, 4):
        with pytest.raises(ac.AuditChainError) as exc:
            c.entry(bad)
        assert exc.value.code == "AUDIT_ENTRY_UNKNOWN"


def test_verify_clean_chain_and_subrange():
    c = chain(keys={"k1": "s1"})
    for index in range(3):
        c.append("svc", "tick", {"index": index}, now=index)
    result = c.verify()
    assert result["ok"] is True
    assert result["checked"] == 3
    assert result["first_bad"] is None
    assert result["code"] is None
    sub = c.verify(start=2, end=3)
    assert sub["ok"] is True
    assert sub["checked"] == 2


def test_tamper_is_located_at_the_first_bad_entry():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    for index in range(4):
        c.append("svc", "tick", {"index": index}, now=index)
    raw[2]["actor"] = "mallory"
    result = c.verify()
    assert result["ok"] is False
    assert result["first_bad"] == 3
    assert result["code"] == "AUDIT_SIGNATURE_MISMATCH"
    assert c.verify(end=2)["ok"] is True


def test_deleted_entry_reports_a_sequence_gap():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    for index in range(4):
        c.append("svc", "tick", {"index": index}, now=index)
    del raw[1]
    result = c.verify()
    assert result["ok"] is False
    assert result["first_bad"] == 2
    assert result["code"] == "AUDIT_SEQ_GAP"
    assert c.verify(start=2)["first_bad"] == 2


def test_invalid_fields_payloads_and_chain_capacity_are_refused():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    with pytest.raises(ac.AuditChainError) as exc:
        c.append("", "act", {}, now=0)
    assert exc.value.code == "AUDIT_FIELD_INVALID"
    with pytest.raises(ac.AuditChainError) as exc:
        c.append("svc", "   ", {}, now=0)
    assert exc.value.code == "AUDIT_FIELD_INVALID"
    for payload in ([1, 2], {1: "x"}, {"bad": object()}):
        with pytest.raises(ac.AuditChainError) as exc:
            c.append("svc", "act", payload, now=0)
        assert exc.value.code == "AUDIT_PAYLOAD_INVALID"
    assert raw == []

    capped = chain(keys={"k1": "s1"}, max_entries=1)
    capped.append("svc", "act", {}, now=0)
    with pytest.raises(ac.AuditChainError) as exc:
        capped.append("svc", "act", {}, now=1)
    assert exc.value.code == "AUDIT_CHAIN_FULL"
    assert capped.stats()["entries"] == 1


def test_key_rotation_keeps_old_entries_verifiable():
    c = chain(keys={"k1": "s1"})
    c.append("svc", "one", {}, now=1)
    c.append("svc", "two", {}, now=2)
    assert c.rotate_key("k2", "s2") == 1
    assert c.active_key == "k2"
    third = c.append("svc", "three", {}, now=3)
    assert third["key_id"] == "k2"
    assert c.entry(1)["key_id"] == "k1"
    assert c.verify()["ok"] is True
    with pytest.raises(ac.AuditChainError) as exc:
        c.rotate_key("k1", "other")
    assert exc.value.code == "AUDIT_KEY_EXISTS"
    assert c.rotate_key("k2", "s2") == 0
    assert c.active_key == "k2"


def test_append_with_missing_active_key_is_refused_until_registered():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"}, active_key="missing")
    with pytest.raises(ac.AuditChainError) as exc:
        c.append("svc", "act", {}, now=0)
    assert exc.value.code == "AUDIT_KEY_UNKNOWN"
    assert raw == []
    assert c.rotate_key("missing", "s3") == 1
    assert c.append("svc", "act", {}, now=0)["key_id"] == "missing"


def test_archive_manifest_segments_and_idempotent_reseal():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"}, segment_size=2)
    for index in range(5):
        c.append("svc", "tick", {"index": index}, now=index)
    manifest = c.seal_archive("a1", upto=5)
    assert manifest["name"] == "a1"
    assert manifest["upto_seq"] == 5
    assert manifest["count"] == 5
    assert manifest["head_sig"] == raw[-1]["sig"]
    assert [(seg["from_seq"], seg["to_seq"]) for seg in manifest["segments"]] == [
        (1, 2), (3, 4), (5, 5)]
    expected = hashlib.sha256((raw[0]["sig"] + raw[1]["sig"]).encode()).hexdigest()
    assert manifest["segments"][0]["digest"] == expected
    assert c.seal_archive("a1", upto=5) == manifest
    with pytest.raises(ac.AuditChainError) as exc:
        c.seal_archive("a1", upto=3)
    assert exc.value.code == "AUDIT_ARCHIVE_CONFLICT"
    assert [item["name"] for item in c.archives()] == ["a1"]


def test_archive_verification_detects_tamper_and_short_chain():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    for index in range(5):
        c.append("svc", "tick", {"index": index}, now=index)
    c.seal_archive("a1")
    assert c.verify_archive("a1")["ok"] is True
    raw[2]["action"] = "rewritten"
    result = c.verify_archive("a1")
    assert result["ok"] is False
    assert result["first_bad"] == 3
    assert result["code"] == "AUDIT_ARCHIVE_MISMATCH"

    raw2 = []
    short = chain(store=raw2, keys={"k1": "s1"})
    for index in range(5):
        short.append("svc", "tick", {"index": index}, now=index)
    short.seal_archive("a2")
    del raw2[-1]
    result = short.verify_archive("a2")
    assert result["ok"] is False
    assert result["first_bad"] == 5
    with pytest.raises(ac.AuditChainError) as exc:
        short.verify_archive("missing")
    assert exc.value.code == "AUDIT_ARCHIVE_UNKNOWN"


def test_truncation_detects_tail_loss_and_middle_gap():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"})
    for index in range(5):
        c.append("svc", "tick", {"index": index}, now=index)
    c.seal_archive("a1")
    del raw[-1]
    tail = c.detect_truncation()
    assert tail["truncated"] is True
    assert tail["reason"] == "tail"
    assert tail["expected"] == 5
    assert tail["actual"] == 4

    raw2 = []
    gapped = chain(store=raw2, keys={"k1": "s1"})
    for index in range(5):
        gapped.append("svc", "tick", {"index": index}, now=index)
    del raw2[2]
    gap = gapped.detect_truncation(expected_entries=5)
    assert gap["truncated"] is True
    assert gap["reason"] == "gap"
    assert gap["first_bad"] == 3

    clean = chain(keys={"k1": "s1"})
    for index in range(3):
        clean.append("svc", "tick", {"index": index}, now=index)
    assert clean.detect_truncation(expected_entries=3)["truncated"] is False


def test_confirm_head_rejects_regression_and_snapshot_roundtrips():
    c = chain(keys={"k1": "s1"})
    for index in range(3):
        c.append("svc", "tick", {"index": index}, now=index)
    sig = c.head()["sig"]
    c.confirm_head(3, sig)
    with pytest.raises(ac.AuditChainError) as exc:
        c.confirm_head(2)
    assert exc.value.code == "AUDIT_HEAD_REGRESSION"
    with pytest.raises(ac.AuditChainError) as exc:
        c.confirm_head(3, "f" * 64)
    assert exc.value.code == "AUDIT_HEAD_CONFLICT"

    state = c.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "entries", "keys", "active_key",
                             "archives", "archive_order", "high_water",
                             "confirmed"}
    state["entries"].clear()
    state["keys"]["k1"] = "tampered"
    assert c.head()["seq"] == 3
    assert c.verify()["ok"] is True

    clone = chain(keys={"k1": "s1"})
    clone.restore(pristine)
    assert clone.head() == c.head()
    assert clone.append("svc", "four", {}, now=4)["seq"] == 4
    assert clone.verify()["ok"] is True

    for bad in (None, {}, {"version": 2}, {"version": 1, "entries": [],
                "keys": {}, "active_key": "k1", "archives": [],
                "archive_order": [], "high_water": 0}):
        with pytest.raises(ac.AuditChainError) as exc:
            clone.restore(bad)
        assert exc.value.code == "AUDIT_SNAPSHOT_INVALID"
    assert clone.head()["seq"] == 4


def test_concurrent_appends_keep_contiguous_unique_sequence():
    raw = []
    c = chain(store=raw, keys={"k1": "s1"}, max_entries=64)
    barrier = threading.Barrier(24)
    errors = []

    def worker(index):
        try:
            barrier.wait(timeout=5)
            c.append(f"actor-{index}", "tick", {"index": index}, now=index)
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert [row["seq"] for row in raw] == list(range(1, 25))
    result = c.verify()
    assert result["ok"] is True
    assert result["checked"] == 24
    assert c.stats()["entries"] == 24
