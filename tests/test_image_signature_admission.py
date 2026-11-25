import copy
import threading

import pytest

import image_signature_admission as ia

DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def gate(**kwargs):
    return ia.ImageSignatureAdmission(**kwargs)


def setup(g, *, threshold=2, percent=100,
          signers=("alice", "bob", "carol")):
    for signer in signers:
        g.register_signer(signer)
    return g.set_policy("payments", signers=signers, threshold=threshold,
                        rollout_percent=percent)


def submit(g, signer="alice", digest=DIGEST_A, key_version=1, at=10,
           repository="payments"):
    return g.submit_signature(repository, digest, signer,
                              key_version=key_version, at=at)


def test_register_signer_sequence_and_duplicate():
    g = gate()
    assert g.register_signer("alice") == 1
    assert g.register_signer(" bob ", key_version=3) == 2
    assert g.signer_of("bob") == ia.SignerView("bob", 2, 3, None, None, False)

    for bad in ["", "   ", 5]:
        with pytest.raises(ia.ImageAdmissionError) as exc:
            g.register_signer(bad)
        assert exc.value.code == "IMAGE_SIGNER_INVALID"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.register_signer("alice")
    assert exc.value.code == "IMAGE_SIGNER_CONFLICT"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.register_signer("dave", key_version=0)
    assert exc.value.code == "IMAGE_SIGNER_INVALID"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.signer_of("ghost")
    assert exc.value.code == "IMAGE_SIGNER_UNKNOWN"


def test_set_policy_validation_and_lookup():
    g = gate()
    for signer in ("alice", "bob", "carol"):
        g.register_signer(signer)

    policy = g.set_policy("payments", signers=("carol", "alice", "bob"),
                          threshold=2, rollout_percent=40)
    assert policy == ia.PolicyView("payments", ("alice", "bob", "carol"), 2,
                                   40)
    assert g.policy_of("payments") == policy

    bad_policies = [
        ("", ("alice",), 1, 0),
        ("payments", (), 1, 0),
        ("payments", ("alice", "bob"), 0, 0),
        ("payments", ("alice", "bob"), 3, 0),
        ("payments", ("alice", "alice"), 1, 0),
        ("payments", ("alice",), 1, 30),
    ]
    for repository, signers, threshold, percent in bad_policies:
        with pytest.raises(ia.ImageAdmissionError) as exc:
            g.set_policy(repository, signers=signers, threshold=threshold,
                         rollout_percent=percent)
        assert exc.value.code == "IMAGE_POLICY_INVALID"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.set_policy("payments", signers=("alice", "zed"), threshold=1)
    assert exc.value.code == "IMAGE_SIGNER_UNKNOWN"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.policy_of("orders")
    assert exc.value.code == "IMAGE_POLICY_UNKNOWN"


def test_submit_signature_validation_and_first_write_wins():
    g = gate()
    setup(g, threshold=2)

    assert submit(g) is True
    assert submit(g, at=20) is False
    assert g.signatures_of("payments", DIGEST_A) == (
        ia.SignatureView("alice", 1, 10, "valid"),
    )

    with pytest.raises(ia.ImageAdmissionError) as exc:
        submit(g, digest="sha256:xyz")
    assert exc.value.code == "IMAGE_DIGEST_INVALID"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        submit(g, signer="ghost")
    assert exc.value.code == "IMAGE_SIGNER_UNKNOWN"

    g.register_signer("dave")
    with pytest.raises(ia.ImageAdmissionError) as exc:
        submit(g, signer="dave")
    assert exc.value.code == "IMAGE_SIGNER_NOT_ALLOWED"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        submit(g, repository="orders")
    assert exc.value.code == "IMAGE_POLICY_UNKNOWN"


def test_threshold_state_counts_distinct_signers():
    g = gate()
    setup(g, threshold=2)
    submit(g, signer="alice")
    submit(g, signer="bob")
    submit(g, signer="alice", at=15)

    assert g.threshold_state("payments", DIGEST_A) == {
        "required": 2,
        "valid": 2,
        "missing": ("carol",),
        "stale": 0,
        "revoked": 0,
        "tainted": 0,
        "met": True,
    }

    empty = g.threshold_state("payments", DIGEST_B)
    assert empty == {
        "required": 2,
        "valid": 0,
        "missing": ("alice", "bob", "carol"),
        "stale": 0,
        "revoked": 0,
        "tainted": 0,
        "met": False,
    }


def test_rotate_signer_invalidates_old_signature_versions():
    g = gate()
    setup(g, threshold=2)
    submit(g, signer="alice")
    submit(g, signer="bob")

    assert g.rotate_signer("alice") == 2
    assert g.threshold_state("payments", DIGEST_A) == {
        "required": 2,
        "valid": 1,
        "missing": ("alice", "carol"),
        "stale": 1,
        "revoked": 0,
        "tainted": 0,
        "met": False,
    }
    assert [(view.signer, view.key_version, view.at, view.state)
            for view in g.signatures_of("payments", DIGEST_A)] == [
        ("alice", 1, 10, "stale_key"),
        ("bob", 1, 10, "valid"),
    ]

    assert submit(g, signer="alice", key_version=2, at=30) is True
    report = g.threshold_state("payments", DIGEST_A)
    assert (report["valid"], report["stale"], report["missing"],
            report["met"]) == (2, 0, ("carol",), True)

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.rotate_signer("ghost")
    assert exc.value.code == "IMAGE_SIGNER_UNKNOWN"


def test_pending_revocation_counts_but_is_tainted():
    g = gate()
    setup(g, threshold=1, percent=100)
    submit(g, signer="alice")

    assert g.revoke_signer("alice", at=100, enforce_after=30) is True
    assert g.revoke_signer("alice", at=101) is False
    assert g.signatures_of("payments", DIGEST_A)[0].state == "pending_revocation"

    report = g.threshold_state("payments", DIGEST_A)
    assert report == {
        "required": 1,
        "valid": 1,
        "missing": ("bob", "carol"),
        "stale": 0,
        "revoked": 0,
        "tainted": 1,
        "met": True,
    }

    decision = g.admit("team-edge", "payments", DIGEST_A, now=110)
    assert (decision.allowed, decision.mode, decision.reason,
            decision.tainted) == (True, "canary", None, True)
    assert decision.decided_at == 110
    assert g.stats()["revocations"] == 1


def test_propagate_enforces_revocation_and_blocks_admission():
    g = gate()
    setup(g, threshold=1)
    submit(g, signer="alice")
    submit(g, signer="bob")
    g.revoke_signer("alice", at=100, enforce_after=30)

    assert g.propagate(129) == ()
    assert g.propagate(130) == ("alice",)
    assert g.propagate(131) == ()

    report = g.threshold_state("payments", DIGEST_A)
    assert (report["valid"], report["revoked"], report["tainted"],
            report["met"]) == (1, 1, 0, True)

    decision = g.admit("team-edge", "payments", DIGEST_A, now=130)
    assert (decision.allowed, decision.mode, decision.reason,
            decision.tainted) == (False, None, "signer_revoked", False)
    assert g.stats()["enforced"] == 1


def test_admit_denies_threshold_and_caches_same_generation():
    g = gate()
    setup(g, threshold=2, percent=100)
    submit(g, signer="alice")

    first = g.admit("team-edge", "payments", DIGEST_A, now=100)
    assert (first.allowed, first.mode, first.reason, first.tainted) == (
        False, None, "threshold_not_met", False)
    second = g.admit("team-edge", "payments", DIGEST_A, now=200)
    assert second == first
    assert g.stats()["evaluations"] == 1
    assert g.stats()["cached_hits"] == 1
    assert g.stats()["denied"] == 1

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.admit("", "payments", DIGEST_A, now=100)
    assert exc.value.code == "IMAGE_NAMESPACE_INVALID"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.admit("team-edge", "orders", DIGEST_A, now=100)
    assert exc.value.code == "IMAGE_POLICY_UNKNOWN"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.admit("team-edge", "payments", "sha256:bad", now=100)
    assert exc.value.code == "IMAGE_DIGEST_INVALID"


def test_rollout_holds_or_admits_by_namespace_bucket():
    g = gate()
    setup(g, threshold=1, percent=20)
    submit(g, signer="alice")

    low = g.admit("team-edge", "payments", DIGEST_A, now=100)
    assert (low.allowed, low.mode, low.reason) == (True, "canary", None)

    high = g.admit("team-search", "payments", DIGEST_A, now=101)
    assert (high.allowed, high.mode, high.reason) == (False, None,
                                                      "rollout_hold")

    assert g.promote("payments", DIGEST_A, now=110) is True
    stable = g.admit("team-search", "payments", DIGEST_A, now=111)
    assert (stable.allowed, stable.mode, stable.reason) == (True, "stable",
                                                            None)


def test_policy_change_invalidates_cached_decisions():
    g = gate()
    setup(g, threshold=1, percent=20)
    submit(g, signer="alice")

    first = g.admit("team-search", "payments", DIGEST_A, now=100)
    assert first.allowed is False
    assert g.stats()["evaluations"] == 1

    again = g.admit("team-search", "payments", DIGEST_A, now=101)
    assert again == first
    assert g.stats()["cached_hits"] == 1

    g.set_policy("payments", signers=("alice", "bob", "carol"), threshold=1,
                 rollout_percent=100)
    third = g.admit("team-search", "payments", DIGEST_A, now=102)
    assert (third.allowed, third.mode) == (True, "canary")
    assert third.generation == first.generation + 1
    assert g.stats()["evaluations"] == 2


def test_promote_requires_threshold_and_records_history():
    g = gate()
    setup(g, threshold=2)
    submit(g, signer="alice")

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.promote("payments", DIGEST_A, now=100)
    assert exc.value.code == "IMAGE_THRESHOLD_NOT_MET"

    submit(g, signer="bob")
    assert g.promote("payments", DIGEST_A, now=110) is True
    assert g.stable_of("payments") == DIGEST_A
    assert g.history("payments") == (DIGEST_A,)
    assert g.promote("payments", DIGEST_A, now=111) is False
    assert g.stats()["promotions"] == 1

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.promote("payments", DIGEST_B, now=112)
    assert exc.value.code == "IMAGE_THRESHOLD_NOT_MET"

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.promote("orders", DIGEST_A, now=113)
    assert exc.value.code == "IMAGE_POLICY_UNKNOWN"


def test_rollback_restores_previous_stable_digest():
    g = gate()
    setup(g, threshold=2)
    for digest in (DIGEST_A, DIGEST_B):
        submit(g, signer="alice", digest=digest)
        submit(g, signer="bob", digest=digest)

    assert g.promote("payments", DIGEST_A, now=100) is True
    assert g.promote("payments", DIGEST_B, now=110) is True
    assert g.history("payments") == (DIGEST_A, DIGEST_B)
    assert g.stable_of("payments") == DIGEST_B

    assert g.rollback("payments", now=200) == DIGEST_A
    assert g.stable_of("payments") == DIGEST_A
    assert g.history("payments") == (DIGEST_A,)
    assert g.stats()["rollbacks"] == 1

    with pytest.raises(ia.ImageAdmissionError) as exc:
        g.rollback("payments", now=201)
    assert exc.value.code == "IMAGE_NO_PREVIOUS_STABLE"


def test_image_record_capacity_is_a_hard_limit():
    g = gate(max_images=1)
    setup(g, threshold=1)
    assert submit(g, signer="alice", digest=DIGEST_A) is True

    with pytest.raises(ia.ImageAdmissionError) as exc:
        submit(g, signer="alice", digest=DIGEST_B)
    assert exc.value.code == "IMAGE_CAPACITY_EXCEEDED"
    assert g.signatures_of("payments", DIGEST_B) == ()


def test_snapshot_restore_roundtrip_and_invalid_state():
    g = gate()
    setup(g, threshold=2, percent=20)
    submit(g, signer="alice")
    submit(g, signer="bob")
    assert g.promote("payments", DIGEST_A, now=100) is True
    g.admit("team-edge", "payments", DIGEST_A, now=110)

    state = g.snapshot()
    assert set(state) == {"version", "policies", "signers", "images",
                          "stable", "history", "generation", "decisions",
                          "counters"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["policies"].clear()
    state["counters"]["evaluations"] = 99
    assert g.policy_of("payments").threshold == 2
    assert g.stats()["evaluations"] == 1

    clone = gate()
    clone.restore(pristine)
    assert clone.stats() == g.stats()
    assert clone.policy_of("payments") == g.policy_of("payments")
    assert clone.threshold_state("payments", DIGEST_A) == \
        g.threshold_state("payments", DIGEST_A)
    assert clone.stable_of("payments") == DIGEST_A
    assert clone.history("payments") == (DIGEST_A,)

    good = g.snapshot()
    missing_key = {key: value for key, value in good.items()
                   if key != "decisions"}
    bad_states = [
        None,
        {},
        dict(good, version=2),
        missing_key,
        dict(good, policies=[]),
        dict(good, counters={}),
    ]
    for bad in bad_states:
        with pytest.raises(ia.ImageAdmissionError) as exc:
            g.restore(bad)
        assert exc.value.code == "IMAGE_SNAPSHOT_INVALID"

    assert g.stable_of("payments") == DIGEST_A
    assert g.stats()["evaluations"] == 1


def test_concurrent_admission_evaluates_once_per_generation():
    g = gate()
    setup(g, threshold=1, percent=100)
    submit(g, signer="alice")

    barrier = threading.Barrier(8)
    decisions = []
    errors = []

    def worker():
        try:
            barrier.wait(timeout=5)
            decisions.append(g.admit("team-edge", "payments", DIGEST_A,
                                     now=100))
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(decisions) == 8
    assert all(decision == decisions[0] for decision in decisions)
    assert g.stats()["evaluations"] == 1
    assert g.stats()["cached_hits"] == 7
    assert g.stats()["admitted"] == 1
