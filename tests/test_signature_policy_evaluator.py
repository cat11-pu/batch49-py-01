import copy
import threading

import pytest

import signature_policy_evaluator as sp


def evaluator():
    return sp.SignaturePolicyEvaluator()


def register(store, key_id="k-1", roles=("release",), usages=("release",),
             not_before=1000, not_after=2000):
    store.register_key(key_id, roles=roles, usages=usages,
                       not_before=not_before, not_after=not_after)


def sig(key_id="k-1", role="release", usage="release", signed_at=1500):
    return sp.Signature(key_id=key_id, role=role, usage=usage,
                        signed_at=signed_at)


def test_global_policy_compiles_into_versioned_snapshot():
    store = evaluator()
    compiled = store.set_global_policy({"threshold": 2,
                                        "roles": ["release"],
                                        "usages": ["release", "nightly"]})
    assert compiled.threshold == 2
    assert compiled.roles == ("release",)
    assert compiled.usages == ("nightly", "release")
    assert compiled.version == 1
    assert compiled.levels == ("global",)
    assert store.compile("org-a", "ns-1") == compiled
    assert store.policy_version("org-a", "ns-1") == 1


def test_namespace_tightens_threshold():
    store = evaluator()
    store.set_global_policy({"threshold": 2, "roles": ["release"],
                             "usages": ["release"]})
    store.set_org_policy("org-a", {"threshold": 3})
    compiled = store.compile("org-a", "ns-1")
    assert compiled.threshold == 3
    assert compiled.roles == ("release",)
    assert compiled.usages == ("release",)
    assert compiled.version == 2
    assert compiled.levels == ("global", "org")


def test_required_roles_are_accumulated_not_replaced():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    compiled = store.set_namespace_policy("org-a", "ns-1",
                                          {"roles": ["security"]})
    assert compiled.roles == ("release", "security")
    assert compiled.levels == ("global", "namespace")
    assert compiled.version == 2


def test_allowed_usages_intersect_downward():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["nightly", "release"]})
    compiled = store.set_org_policy("org-a", {"usages": ["release"]})
    assert compiled.usages == ("release",)
    assert compiled.levels == ("global", "org")


def test_invalid_policy_is_rejected_and_leaves_no_snapshot():
    store = evaluator()
    bad_policies = [
        {"threshold": 0},
        {"threshold": 2, "roles": [""]},
        {"threshold": 2, "usages": "release"},
        {"roles": ["release"]},
        {"threshold": 2, "retries": 3},
    ]
    for policy in bad_policies:
        with pytest.raises(sp.PolicyError) as exc:
            store.set_global_policy(policy)
        assert exc.value.code == "POLICY_INVALID"
    with pytest.raises(sp.PolicyError) as exc:
        store.compile("org-a", "ns-1")
    assert exc.value.code == "POLICY_NOT_CONFIGURED"


def test_lower_layers_require_global_policy():
    store = evaluator()
    with pytest.raises(sp.PolicyError) as exc:
        store.compile("org-a", "ns-1")
    assert exc.value.code == "POLICY_NOT_CONFIGURED"
    with pytest.raises(sp.PolicyError) as exc:
        store.set_org_policy("org-a", {"threshold": 2})
    assert exc.value.code == "POLICY_NOT_CONFIGURED"
    with pytest.raises(sp.PolicyError) as exc:
        store.set_namespace_policy("org-a", "ns-1", {"threshold": 2})
    assert exc.value.code == "POLICY_NOT_CONFIGURED"


def test_register_key_bumps_status_version_only_on_success():
    store = evaluator()
    register(store, "k-1")
    assert store.key_status_version() == 1
    register(store, "k-2", roles=("release", "security"))
    assert store.key_status_version() == 2
    bad_calls = [
        dict(key_id=""),
        dict(key_id="k-3", not_before=2000, not_after=1000),
    ]
    for kwargs in bad_calls:
        with pytest.raises(sp.PolicyError) as exc:
            store.register_key(kwargs["key_id"],
                               roles=("release",), usages=("release",),
                               not_before=kwargs.get("not_before", 1000),
                               not_after=kwargs.get("not_after", 2000))
        assert exc.value.code == "POLICY_INVALID"
    assert store.key_status_version() == 2


def test_evaluate_counts_distinct_effective_signers():
    store = evaluator()
    store.set_global_policy({"threshold": 2, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1")
    register(store, "k-2")
    result = store.evaluate("org-a", "ns-1",
                            [sig("k-1"), sig("k-2", signed_at=1600),
                             sig("k-1", signed_at=1700)], at=1800)
    assert result.effective == 2
    assert result.threshold == 2
    assert result.satisfied is True
    assert result.unmet == ()
    assert result.rejected == ()


def test_evaluate_reports_unmet_requirements():
    store = evaluator()
    store.set_global_policy({"threshold": 3, "roles": ["release"],
                             "usages": ["release"]})
    for key_id in ("k-1", "k-2", "k-3"):
        register(store, key_id)
    result = store.evaluate("org-a", "ns-1",
                            [sig("k-1"), sig("k-2"), sig("k-3", role="nightly")],
                            at=1800)
    assert result.effective == 2
    assert result.satisfied is False
    assert result.unmet == ("THRESHOLD_SHORT",)
    assert result.rejected == ("ROLE_UNMET",)


def test_signature_before_key_validity_rejected():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1", not_before=1000)
    result = store.evaluate("org-a", "ns-1", [sig("k-1", signed_at=999)],
                            at=1800)
    assert result.effective == 0
    assert result.rejected == ("KEY_NOT_YET_VALID",)
    assert result.satisfied is False


def test_unregistered_key_rejected():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1")
    result = store.evaluate("org-a", "ns-1",
                            [sig("ghost"), sig("k-1")], at=1800)
    assert result.rejected == ("KEY_UNKNOWN",)
    assert result.effective == 1


def test_duplicate_signatures_count_once():
    store = evaluator()
    store.set_global_policy({"threshold": 2, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1")
    result = store.evaluate("org-a", "ns-1",
                            [sig("k-1", signed_at=1400),
                             sig("k-1", signed_at=1500),
                             sig("k-1", signed_at=1600)], at=1800)
    assert result.effective == 1
    assert result.satisfied is False
    assert result.rejected == ()


def test_evaluation_records_versions_and_cache_hit():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1")
    compiled = store.compile("org-a", "ns-1")
    signatures = [sig("k-1")]
    first = store.evaluate("org-a", "ns-1", signatures, at=1800)
    assert first.policy_version == compiled.version
    assert first.key_status_version == store.key_status_version() == 1
    second = store.evaluate("org-a", "ns-1", list(signatures), at=1800)
    assert second == first
    assert store.cache_size() == 1
    assert store.stats() == {"policy_sets": 1, "keys_registered": 1,
                             "evaluations": 2, "cache_hits": 1}


def test_snapshot_restore_preserves_policies_keys_and_versions():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    store.set_org_policy("org-a", {"threshold": 2})
    register(store, "k-1", roles=("release", "security"))
    store.evaluate("org-a", "ns-1", [sig("k-1")], at=1800)
    state = store.snapshot()
    assert set(state) == {"version", "policies", "keys", "key_status_version",
                          "cache", "stats"}
    pristine = copy.deepcopy(state)
    state["keys"].clear()
    assert store.compile("org-a", "ns-1").threshold == 2

    clone = evaluator()
    clone.restore(pristine)
    assert clone.compile("org-a", "ns-1").threshold == 2
    assert clone.key_status_version() == 1
    assert clone.evaluate("org-a", "ns-1", [sig("k-1")],
                          at=1800).satisfied is True

    with pytest.raises(sp.PolicyError) as exc:
        clone.restore({"version": 1})
    assert exc.value.code == "POLICY_SNAPSHOT_INVALID"
    assert clone.compile("org-a", "ns-1").threshold == 2


def test_concurrent_evaluations_are_consistent():
    store = evaluator()
    store.set_global_policy({"threshold": 1, "roles": ["release"],
                             "usages": ["release"]})
    register(store, "k-1")
    results = []
    errors = []
    gate = threading.Barrier(8)

    def worker():
        try:
            gate.wait(timeout=5)
            results.append(store.evaluate("org-a", "ns-1", [sig("k-1")],
                                          at=1800))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 8
    assert all(row == results[0] for row in results)
    assert results[0].satisfied is True
    assert store.stats()["evaluations"] == 8
    assert store.stats()["cache_hits"] == 7
