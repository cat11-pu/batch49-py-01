import copy
import hashlib
import hmac
import json
import threading
import time

import pytest

import rollback_bundle_guard as rg

MATERIAL = "k1-secret"


def asset(asset_id, from_version, to_version):
    return {"asset_id": asset_id, "from_version": from_version,
            "to_version": to_version}


def sign(batch_id, items, material=MATERIAL):
    body = {"batch_id": batch_id, "assets": items}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return hmac.new(material.encode(), text.encode(), hashlib.sha256).hexdigest()


def make_batch(batch_id, triples, *, key_id="k1", material=MATERIAL):
    items = [asset(*triple) for triple in triples]
    return {"batch_id": batch_id, "key_id": key_id, "assets": items,
            "signature": sign(batch_id, items, material)}


def make_bundle(*batches, bundle_id="b1"):
    return {"bundle_id": bundle_id, "batches": list(batches)}


def guard(fail=None, **kwargs):
    recorded = []
    fails = dict(fail or {})

    def undo(asset_id, from_version, to_version):
        recorded.append((asset_id, from_version, to_version))
        return not fails.get(asset_id, False)

    g = rg.RollbackBundleGuard(undo, **kwargs)
    g.trust_key("k1", MATERIAL)
    return g, recorded, fails


def test_submit_accepts_signed_batches_and_reports_status():
    g, _, _ = guard()
    bundle = make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)]),
                         make_batch("bB", [("z", 2, 1)]))
    result = g.submit(bundle, request_id="r1", now=10)
    assert result["accepted"] == 2
    assert result["quarantined"] == ()
    assert result["total_batches"] == 2
    assert result["bundle_id"] == "b1"

    status = g.bundle_status("r1")
    assert status["executed"] is False
    assert status["status"] == "pending"
    assert [(row["batch_id"], row["status"], row["attempts"])
            for row in status["batches"]] == [
        ("bA", "trusted", 0), ("bB", "trusted", 0)]


def test_verify_batch_recomputes_signature_and_reports_codes():
    g, _, _ = guard()
    batch = make_batch("bA", [("x", 5, 4)])
    ok = g.verify_batch(batch)
    assert ok["status"] == "ok"
    assert ok["code"] is None
    assert ok["batch_id"] == "bA"

    tampered = copy.deepcopy(batch)
    tampered["assets"][0]["to_version"] = 3
    bad = g.verify_batch(tampered)
    assert bad["status"] == "quarantined"
    assert bad["code"] == "ROLLBACK_SIGNATURE_INVALID"

    unknown = make_batch("bA", [("x", 5, 4)], key_id="k9")
    assert g.verify_batch(unknown)["code"] == "ROLLBACK_KEY_UNKNOWN"

    malformed = {"batch_id": "bA", "key_id": "k1", "assets": [],
                 "signature": "deadbeef"}
    assert g.verify_batch(malformed)["code"] == "ROLLBACK_BATCH_INVALID"


def test_execute_runs_batches_and_assets_in_declared_order():
    g, recorded, _ = guard()
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)]),
                         make_batch("bB", [("z", 2, 1)])),
             request_id="r1")
    result = g.execute("r1", now=20)
    assert result["status"] == "completed"
    assert result["undone"] == ("x", "y", "z")
    assert result["done"] == ("bA", "bB")
    assert result["failed"] == ()
    assert result["quarantined"] == ()
    assert recorded == [("x", 5, 4), ("y", 5, 4), ("z", 2, 1)]
    assert g.bundle_status("r1")["executed"] is True
    assert g.stats()["undone"] == 3


def test_bad_signature_batch_is_quarantined_while_others_execute():
    g, recorded, _ = guard()
    bad = make_batch("bB", [("z", 2, 1)])
    bad["assets"][0]["to_version"] = 9
    submitted = g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)]),
                                     bad), request_id="r1")
    assert submitted["accepted"] == 1
    assert submitted["quarantined"] == (
        {"batch_id": "bB", "code": "ROLLBACK_SIGNATURE_INVALID"},)
    result = g.execute("r1")
    assert result["status"] == "partial"
    assert result["undone"] == ("x", "y")
    assert result["done"] == ("bA",)
    assert result["quarantined"] == (
        {"batch_id": "bB", "code": "ROLLBACK_SIGNATURE_INVALID"},)
    assert recorded == [("x", 5, 4), ("y", 5, 4)]


def test_undo_returning_false_fails_only_that_batch():
    g, recorded, _ = guard(fail={"y": True})
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)]),
                         make_batch("bB", [("z", 2, 1)])),
             request_id="r1")
    result = g.execute("r1")
    assert result["status"] == "partial"
    assert result["undone"] == ("x", "z")
    assert result["done"] == ("bB",)
    assert result["failed"] == (
        {"batch_id": "bA", "code": "ROLLBACK_UNDO_FAILED", "asset_id": "y"},)
    assert recorded == [("x", 5, 4), ("y", 5, 4), ("z", 2, 1)]


def test_undo_exception_is_captured_and_other_batches_continue():
    recorded = []

    def undo(asset_id, from_version, to_version):
        recorded.append((asset_id, from_version, to_version))
        if asset_id == "y":
            raise RuntimeError("disk full")
        return True

    g = rg.RollbackBundleGuard(undo)
    g.trust_key("k1", MATERIAL)
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)])),
             request_id="r1")
    result = g.execute("r1")
    assert result["status"] == "failed"
    assert result["undone"] == ("x",)
    assert result["failed"][0]["asset_id"] == "y"
    assert recorded == [("x", 5, 4), ("y", 5, 4)]


def test_asset_repeated_in_later_batch_is_quarantined():
    g, recorded, _ = guard()
    bundle = make_bundle(make_batch("bA", [("x", 5, 4)]),
                         make_batch("bB", [("x", 5, 4), ("y", 2, 1)]))
    submitted = g.submit(bundle, request_id="r1")
    assert submitted["accepted"] == 1
    assert submitted["quarantined"] == (
        {"batch_id": "bB", "code": "ROLLBACK_ASSET_DUPLICATE"},)
    result = g.execute("r1")
    assert result["undone"] == ("x",)
    assert result["done"] == ("bA",)
    assert recorded == [("x", 5, 4)]


def test_execute_is_idempotent_and_unknown_request_is_refused():
    g, recorded, _ = guard()
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)])),
             request_id="r1")
    first = g.execute("r1")
    second = g.execute("r1")
    assert second == first
    assert recorded == [("x", 5, 4), ("y", 5, 4)]
    with pytest.raises(rg.RollbackError) as exc:
        g.execute("missing")
    assert exc.value.code == "ROLLBACK_REQUEST_UNKNOWN"


def test_submit_replay_is_idempotent_and_payload_change_conflicts():
    g, _, _ = guard()
    bundle = make_bundle(make_batch("bA", [("x", 5, 4)]),
                         make_batch("bB", [("y", 2, 1)]))
    first = g.submit(bundle, request_id="r1")
    second = g.submit(bundle, request_id="r1")
    assert second == first
    assert len(g.bundle_status("r1")["batches"]) == 2
    other = make_bundle(make_batch("bZ", [("q", 1, 0)]), bundle_id="b2")
    with pytest.raises(rg.RollbackError) as exc:
        g.submit(other, request_id="r1")
    assert exc.value.code == "ROLLBACK_REQUEST_CONFLICT"


def test_retry_resumes_from_failed_asset_without_repeating_done_ones():
    g, recorded, fails = guard(fail={"y": True})
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4),
                                          ("z", 5, 4)])), request_id="r1")
    first = g.execute("r1")
    assert first["status"] == "failed"
    assert first["undone"] == ("x",)
    fails["y"] = False
    retried = g.retry_batch("r1", "bA")
    assert retried["status"] == "done"
    assert retried["attempts"] == 2
    assert retried["undone"] == ("y", "z")
    assert recorded == [("x", 5, 4), ("y", 5, 4),
                        ("y", 5, 4), ("z", 5, 4)]
    final = g.execute("r1")
    assert final["status"] == "completed"
    assert final["undone"] == ("x", "y", "z")
    assert final["failed"] == ()


def test_retry_attempt_cap_and_wrong_batch_states():
    g, _, _ = guard(fail={"y": True}, max_attempts=2)
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)]),
                         make_batch("bB", [("z", 2, 1)])), request_id="r1")
    g.execute("r1")
    retried = g.retry_batch("r1", "bA")
    assert retried["status"] == "failed"
    assert retried["attempts"] == 2
    with pytest.raises(rg.RollbackError) as exc:
        g.retry_batch("r1", "bA")
    assert exc.value.code == "ROLLBACK_ATTEMPTS_EXCEEDED"
    with pytest.raises(rg.RollbackError) as exc:
        g.retry_batch("r1", "bB")
    assert exc.value.code == "ROLLBACK_BATCH_NOT_FAILED"
    with pytest.raises(rg.RollbackError) as exc:
        g.retry_batch("r1", "missing")
    assert exc.value.code == "ROLLBACK_BATCH_UNKNOWN"


def test_bundle_structure_and_limits_are_refused_atomically():
    g, _, _ = guard(max_assets_per_batch=2)
    invalid = [
        make_bundle(bundle_id="empty"),
        {"bundle_id": "b1", "batches": [make_batch("bA", [("x", 5, 4)]),
                                        make_batch("bA", [("y", 5, 4)])]},
        make_bundle({"batch_id": "bA", "key_id": "k1", "assets": [],
                     "signature": "x"}),
        make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4), ("z", 5, 4)])),
        make_bundle(make_batch("bA", [("x", 5, 5)])),
        make_bundle({"batch_id": "bA", "key_id": "k1",
                     "assets": [{"asset_id": "x", "from_version": "5",
                                 "to_version": 4}],
                     "signature": "x"}),
    ]
    for bundle in invalid:
        with pytest.raises(rg.RollbackError) as exc:
            g.submit(bundle, request_id="bad")
        assert exc.value.code == "ROLLBACK_BUNDLE_INVALID"
    with pytest.raises(rg.RollbackError) as exc:
        g.bundle_status("bad")
    assert exc.value.code == "ROLLBACK_REQUEST_UNKNOWN"


def test_settled_asset_is_quarantined_in_a_later_request():
    g, recorded, _ = guard()
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4)])), request_id="r1")
    assert g.execute("r1")["status"] == "completed"

    second = make_bundle(make_batch("bC", [("x", 5, 4)]),
                         make_batch("bD", [("y", 1, 0)]), bundle_id="b2")
    submitted = g.submit(second, request_id="r2")
    assert submitted["accepted"] == 1
    assert submitted["quarantined"] == (
        {"batch_id": "bC", "code": "ROLLBACK_ASSET_SETTLED"},)
    result = g.execute("r2")
    assert result["undone"] == ("y",)
    assert result["done"] == ("bD",)
    assert recorded == [("x", 5, 4), ("y", 1, 0)]


def test_snapshot_restore_preserves_idempotency_and_retry():
    g, recorded, _ = guard(fail={"y": True})
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4),
                                          ("z", 5, 4)]),
                         make_batch("bB", [("w", 1, 0)])), request_id="r1")
    first = g.execute("r1")
    assert first["status"] == "partial"

    state = g.snapshot()
    pristine = copy.deepcopy(state)
    assert set(pristine) == {"version", "keys", "requests", "settled",
                             "history", "stats"}
    state["requests"].clear()
    state["stats"]["undone"] = 0
    assert g.bundle_status("r1")["status"] == "partial"

    recorded2 = []

    def undo2(asset_id, from_version, to_version):
        recorded2.append((asset_id, from_version, to_version))
        return True

    clone = rg.RollbackBundleGuard(undo2, max_attempts=2)
    clone.restore(pristine)
    cached = clone.execute("r1")
    assert cached["status"] == "partial"
    assert recorded2 == []
    retried = clone.retry_batch("r1", "bA")
    assert retried["status"] == "done"
    assert recorded2 == [("y", 5, 4), ("z", 5, 4)]
    assert clone.execute("r1")["status"] == "completed"

    for bad in (None, {}, {"version": 2},
                {"version": 1, "keys": {}, "requests": [],
                 "settled": {}, "history": [], "stats": {}}):
        with pytest.raises(rg.RollbackError) as exc:
            clone.restore(bad)
        assert exc.value.code == "ROLLBACK_SNAPSHOT_INVALID"
    assert clone.bundle_status("r1")["batches"][0]["status"] == "done"


def test_concurrent_execute_runs_each_asset_once():
    recorded = []

    def undo(asset_id, from_version, to_version):
        time.sleep(0.03)
        recorded.append((asset_id, from_version, to_version))
        return True

    g = rg.RollbackBundleGuard(undo)
    g.trust_key("k1", MATERIAL)
    g.submit(make_bundle(make_batch("bA", [("x", 5, 4), ("y", 5, 4)])),
             request_id="r1")
    barrier = threading.Barrier(10)
    results = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(g.execute("r1", now=1))
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(results) == 10
    assert recorded == [("x", 5, 4), ("y", 5, 4)]
    assert all(row == results[0] for row in results)
    assert g.stats()["undone"] == 2
