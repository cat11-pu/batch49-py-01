import copy
import hashlib
import hmac
import json

import pytest

import waf_rule_release_guard as wg

MATERIAL = "edge-signing-material-01"
KEY_ID = "edge-key"
GENESIS = "0" * 64


def _canon(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def _rule(rule_id, pattern="union select", action="block", priority=50):
    return {"rule_id": rule_id, "pattern": pattern, "action": action,
            "priority": priority}


def _add(rule_id, **kwargs):
    return {"op": "add", "rule": _rule(rule_id, **kwargs)}


def _update(rule_id, **kwargs):
    return {"op": "update", "rule": _rule(rule_id, **kwargs)}


def _remove(rule_id):
    return {"op": "remove", "rule": {"rule_id": rule_id}}


def _raw(batch_id, percent, ops):
    return {"batch_id": batch_id, "percent": percent,
            "ops": copy.deepcopy(ops), "prev_digest": GENESIS,
            "signature": "0" * 64}


def _signed_batch(release_id, batch_id, percent, prev_digest, ops, *,
                  key_id=KEY_ID, material=MATERIAL):
    body = {
        "release_id": release_id,
        "batch_id": batch_id,
        "percent": percent,
        "prev_digest": prev_digest,
        "ops": copy.deepcopy(ops),
    }
    raw = _canon(body)
    digest = hashlib.sha256(raw).hexdigest()
    signature = hmac.new(material.encode("utf-8"), raw,
                         hashlib.sha256).hexdigest()
    return {
        "batch_id": batch_id,
        "percent": percent,
        "ops": copy.deepcopy(ops),
        "prev_digest": prev_digest,
        "signature": signature,
    }, digest


def _release(release_id, stages, *, key_id=KEY_ID, material=MATERIAL):
    batches = []
    prev = GENESIS
    for index, (percent, ops) in enumerate(stages, start=1):
        batch, prev = _signed_batch(release_id, "b%d" % index, percent, prev,
                                    ops, key_id=key_id, material=material)
        batches.append(batch)
    return {"release_id": release_id, "key_id": key_id, "batches": batches}


def _guard(**kwargs):
    guard = wg.WafRuleReleaseGuard(**kwargs)
    guard.trust_key(KEY_ID, MATERIAL)
    return guard


def _ids(rules):
    return [item["rule_id"] for item in rules]


def test_trust_key_requires_material_and_tracks_registration():
    guard = wg.WafRuleReleaseGuard()
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.trust_key("short", "tiny")
    assert exc.value.code == "RELEASE_KEY_INVALID"
    assert guard.trust_key(KEY_ID, MATERIAL) == KEY_ID
    assert guard.untrust_key("missing") is False
    assert guard.untrust_key(KEY_ID) is True
    assert guard.untrust_key(KEY_ID) is False


def test_verify_release_accepts_signed_chain_and_compiles_ops():
    guard = _guard()
    release = _release("rel-a", [
        (50, [_add("r2"), _add("r1", priority=70)]),
        (100, [_update("r1", pattern="exec\\(", priority=90),
               _remove("r2"),
               _add("r3", action="log", priority=10)]),
    ])
    result = guard.verify_release(release)
    assert result["release_id"] == "rel-a"
    assert result["batch_ids"] == ["b1", "b2"]
    assert result["percent"] == 100
    assert result["rules"] == 2
    assert result["valid"] is True
    assert len(result["digests"]) == 2


def test_verify_release_rejects_bad_structure_before_signature():
    guard = wg.WafRuleReleaseGuard()
    ops = [_add("r1")]
    cases = [
        {"release_id": "rel", "key_id": KEY_ID, "batches": []},
        {"release_id": "rel", "key_id": KEY_ID,
         "batches": [_raw("b1", 50, ops), _raw("b1", 100, ops)]},
        {"release_id": "rel", "key_id": KEY_ID,
         "batches": [_raw("b1", 0, ops)]},
        {"release_id": "rel", "key_id": KEY_ID,
         "batches": [_raw("b1", 30, ops), _raw("b2", 100, ops)]},
        {"release_id": "rel", "key_id": KEY_ID,
         "batches": [_raw("b1", 40, ops), _raw("b2", 80, ops)]},
        {"release_id": "", "key_id": KEY_ID,
         "batches": [_raw("b1", 100, ops)]},
    ]
    for release in cases:
        with pytest.raises(wg.WafRuleReleaseError) as exc:
            guard.verify_release(release)
        assert exc.value.code == "RELEASE_INVALID"


def test_verify_release_rejects_unknown_key_and_tampered_payload():
    guard = wg.WafRuleReleaseGuard()
    guard.trust_key("other-key", MATERIAL)
    release = _release("rel-a", [(100, [_add("r1")])])
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.verify_release(release)
    assert exc.value.code == "RELEASE_KEY_UNKNOWN"
    guard.trust_key(KEY_ID, MATERIAL)
    release["batches"][0]["ops"][0]["rule"]["pattern"] = "tampered"
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.verify_release(release)
    assert exc.value.code == "RELEASE_SIGNATURE_INVALID"


def test_submit_rejects_illegal_rule_operations_without_registering():
    guard = _guard()
    bad_ops = [
        [_add("r1"), _add("r1")],
        [_update("missing")],
        [_remove("missing")],
        [_add("r4", action="drop")],
        [_add("r5", priority=0)],
    ]
    for index, ops in enumerate(bad_ops):
        request_id = "rq-bad-%d" % index
        release = _release("rel-bad-%d" % index, [(100, ops)])
        with pytest.raises(wg.WafRuleReleaseError) as exc:
            guard.submit(release, request_id=request_id, now=10 + index)
        assert exc.value.code == "RELEASE_RULESET_INVALID"
        with pytest.raises(wg.WafRuleReleaseError) as status_exc:
            guard.release_status(request_id)
        assert status_exc.value.code == "RELEASE_REQUEST_UNKNOWN"


def test_release_caps_are_hard_limits():
    guard = _guard(max_rules=3, max_batches=2)
    too_many_rules = _release("rel-rules", [(100, [
        _add("r1"), _add("r2"), _add("r3"), _add("r4")])])
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.verify_release(too_many_rules)
    assert exc.value.code == "RELEASE_LIMIT_EXCEEDED"
    too_many_batches = _release("rel-batches", [
        (20, [_add("a1")]), (60, [_add("a2")]), (100, [_add("a3")])])
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.submit(too_many_batches, request_id="rq-cap")
    assert exc.value.code == "RELEASE_LIMIT_EXCEEDED"
    with pytest.raises(wg.WafRuleReleaseError) as status_exc:
        guard.release_status("rq-cap")
    assert status_exc.value.code == "RELEASE_REQUEST_UNKNOWN"


def test_submit_stages_release_and_replays_identical_request():
    guard = _guard()
    release = _release("rel-a", [
        (50, [_add("r1"), _add("r2")]),
        (100, [_add("r3")]),
    ])
    first = guard.submit(release, request_id="rq-1", now=100)
    assert first["release_id"] == "rel-a"
    assert first["status"] == "staged"
    assert first["percent"] == 0
    assert first["generation"] == 1
    assert first["rules"] == 3
    assert first["replayed"] is False
    replay = guard.submit(copy.deepcopy(release), request_id="rq-1", now=101)
    assert replay["status"] == "staged"
    assert replay["replayed"] is True
    assert replay["generation"] == 1
    assert replay["rules"] == 3
    status = guard.release_status("rq-1")
    assert status["status"] == "staged"
    assert status["percent"] == 0
    assert status["generation"] == 1
    assert status["rules"] == 3
    assert guard.active_ruleset()["generation"] == 1
    assert guard.active_ruleset()["rules"] == []


def test_submit_conflicts_on_different_content_for_same_request():
    guard = _guard()
    guard.submit(_release("rel-a", [(100, [_add("r1")])]),
                 request_id="rq-1")
    second = _release("rel-b", [(100, [_add("r1"), _add("r2")])])
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.submit(second, request_id="rq-1", now=5)
    assert exc.value.code == "RELEASE_REQUEST_CONFLICT"
    status = guard.release_status("rq-1")
    assert status["release_id"] == "rel-a"
    assert status["rules"] == 1


def test_promote_applies_gray_percent_to_shard_prefix():
    guard = _guard()
    release = _release("rel-a", [
        (20, [_add("r1")]),
        (40, [_add("r2")]),
        (100, [_add("r3")]),
    ])
    guard.submit(release, request_id="rq-1")
    result = guard.promote("rq-1", percent=40, now=10)
    assert result["percent"] == 40
    assert result["generation"] == 2
    assert result["shards"] == list(range(8))
    covered = guard.shard_ruleset(7)
    assert covered["generation"] == 2
    assert covered["release_id"] == "rel-a"
    assert _ids(covered["rules"]) == ["r1", "r2"]
    stable = guard.shard_ruleset(8)
    assert stable["generation"] == 1
    assert stable["release_id"] is None
    assert stable["rules"] == []
    assert guard.shard_ruleset(19)["generation"] == 1
    assert guard.active_ruleset()["generation"] == 1


def test_promote_rejects_regression_bad_step_and_unknown_request():
    guard = _guard()
    guard.submit(_release("rel-a", [(100, [_add("r1")])]),
                 request_id="rq-1")
    guard.promote("rq-1", percent=20)
    for bad in (30, 10, 120):
        with pytest.raises(wg.WafRuleReleaseError) as exc:
            guard.promote("rq-1", percent=bad)
        assert exc.value.code == "RELEASE_PERCENT_INVALID"
    assert guard.release_status("rq-1")["percent"] == 20
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.promote("rq-missing", percent=40)
    assert exc.value.code == "RELEASE_REQUEST_UNKNOWN"
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.commit("rq-missing")
    assert exc.value.code == "RELEASE_REQUEST_UNKNOWN"


def test_commit_requires_full_rollout_then_switches_every_shard():
    guard = _guard()
    release = _release("rel-a", [
        (60, [_add("r1")]),
        (100, [_add("r2")]),
    ])
    guard.submit(release, request_id="rq-1")
    guard.promote("rq-1", percent=60)
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.commit("rq-1")
    assert exc.value.code == "RELEASE_NOT_READY"
    guard.promote("rq-1", percent=100)
    result = guard.commit("rq-1", now=30)
    assert result["status"] == "committed"
    assert result["generation"] == 2
    assert result["percent"] == 100
    assert result["rules"] == 2
    active = guard.active_ruleset()
    assert active["generation"] == 2
    assert active["release_id"] == "rel-a"
    assert _ids(active["rules"]) == ["r1", "r2"]
    for shard in (0, 10, 19):
        view = guard.shard_ruleset(shard)
        assert view["generation"] == 2
        assert _ids(view["rules"]) == ["r1", "r2"]
    assert guard.release_status("rq-1")["status"] == "committed"


def test_terminal_releases_reject_further_promote_and_commit():
    guard = _guard()
    guard.submit(_release("rel-a", [(100, [_add("r1")])]),
                 request_id="rq-a")
    guard.promote("rq-a", percent=100)
    guard.commit("rq-a")
    for call in (lambda: guard.promote("rq-a", percent=100),
                 lambda: guard.commit("rq-a")):
        with pytest.raises(wg.WafRuleReleaseError) as exc:
            call()
        assert exc.value.code == "RELEASE_STATE_INVALID"
    guard.submit(_release("rel-b", [(100, [_add("r2")])]),
                 request_id="rq-b")
    guard.promote("rq-b", percent=100)
    guard.commit("rq-b")
    guard.rollback("rq-b")
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.commit("rq-b")
    assert exc.value.code == "RELEASE_STATE_INVALID"
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.promote("rq-b", percent=100)
    assert exc.value.code == "RELEASE_STATE_INVALID"


def test_rollback_discards_staged_candidate_without_bumping_generation():
    guard = _guard()
    release = _release("rel-a", [
        (50, [_add("r1")]),
        (100, [_add("r2")]),
    ])
    guard.submit(release, request_id="rq-1")
    guard.promote("rq-1", percent=50)
    result = guard.rollback("rq-1", now=20)
    assert result["status"] == "rolled_back"
    assert result["generation"] == 1
    assert result["rules"] == 0
    assert guard.active_ruleset()["generation"] == 1
    assert guard.shard_ruleset(0)["generation"] == 1
    assert guard.shard_ruleset(0)["rules"] == []
    again = guard.rollback("rq-1")
    assert again["status"] == "rolled_back"
    assert again["replayed"] is True
    assert again["generation"] == 1
    guard.submit(_release("rel-b", [(100, [_add("r9")])]),
                 request_id="rq-2")
    assert guard.release_status("rq-2")["status"] == "staged"


def test_rollback_after_commit_restores_previous_ruleset():
    guard = _guard()
    first = _release("rel-a", [(100, [
        _add("r1"), _add("r2", priority=60)])])
    guard.submit(first, request_id="rq-a")
    guard.promote("rq-a", percent=100)
    guard.commit("rq-a")
    second = _release("rel-b", [(100, [
        _update("r1", pattern="exec\\(", priority=70),
        _remove("r2"),
        _add("r3", priority=10),
    ])])
    guard.submit(second, request_id="rq-b")
    guard.promote("rq-b", percent=100)
    guard.commit("rq-b")
    assert _ids(guard.active_ruleset()["rules"]) == ["r1", "r3"]
    result = guard.rollback("rq-b", now=40)
    assert result["status"] == "rolled_back"
    assert result["generation"] == 4
    active = guard.active_ruleset()
    assert active["generation"] == 4
    assert _ids(active["rules"]) == ["r2", "r1"]
    view = guard.shard_ruleset(5)
    assert view["generation"] == 4
    assert _ids(view["rules"]) == ["r2", "r1"]


def test_snapshot_restore_resumes_staged_release_and_rejects_bad_state():
    guard = _guard()
    release = _release("rel-a", [
        (40, [_add("r1")]),
        (100, [_add("r2")]),
    ])
    guard.submit(release, request_id="rq-1")
    guard.promote("rq-1", percent=40)
    state = guard.snapshot()
    assert isinstance(state, dict)
    guard.restore(state)
    assert guard.release_status("rq-1")["percent"] == 40
    guard.promote("rq-1", percent=100)
    committed = guard.commit("rq-1")
    assert committed["generation"] == 2
    assert _ids(guard.active_ruleset()["rules"]) == ["r1", "r2"]
    with pytest.raises(wg.WafRuleReleaseError) as exc:
        guard.restore({"version": 999})
    assert exc.value.code == "RELEASE_SNAPSHOT_INVALID"
    assert _ids(guard.active_ruleset()["rules"]) == ["r1", "r2"]
