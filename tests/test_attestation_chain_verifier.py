import copy
import threading

import pytest

import attestation_chain_verifier as ac


def verifier(**kwargs):
    return ac.AttestationChainVerifier(**kwargs)


def add_root(store, key_id, owner, secret, not_before=0, not_after=5000):
    store.add_root(key_id, owner=owner, secret=secret,
                   not_before=not_before, not_after=not_after)


def envelope(eid, seq, issuer, subject, key_id, signed_at, secret,
             payload="payload-1"):
    return ac.Envelope(envelope_id=eid, seq=seq, issuer=issuer, subject=subject,
                       key_id=key_id, signed_at=signed_at, payload_sha=payload,
                       sig=ac.sign_envelope(payload, key_id, secret))


def three_ring_setup(**kwargs):
    store = verifier(**kwargs)
    add_root(store, "k-0", "ca", "s0")
    add_root(store, "k-1", "svc-a", "s1")
    add_root(store, "k-2", "svc-b", "s2")
    chain = [
        envelope("e-0", 0, "ca", "svc-a", "k-0", 100, "s0"),
        envelope("e-1", 1, "svc-a", "svc-b", "k-1", 110, "s1"),
        envelope("e-2", 2, "svc-b", "svc-c", "k-2", 120, "s2"),
    ]
    return store, chain


def test_sign_envelope_is_deterministic():
    first = ac.sign_envelope("payload-1", "k-0", "s0")
    second = ac.sign_envelope("payload-1", "k-0", "s0")
    assert first == second
    assert len(first) == 64
    assert all(char in "0123456789abcdef" for char in first)
    assert ac.sign_envelope("payload-1", "k-1", "s0") != first
    assert ac.sign_envelope("payload-2", "k-0", "s0") != first


def test_add_root_registers_key_and_bumps_root_version():
    store = verifier()
    add_root(store, "k-0", "ca", "s0")
    assert store.root_version() == 1
    assert store.active_keys(100) == ["k-0"]
    with pytest.raises(ac.AttestationError) as exc:
        add_root(store, "k-0", "ca", "s0")
    assert exc.value.code == "ATTEST_INVALID"
    with pytest.raises(ac.AttestationError) as exc:
        add_root(store, "", "ca", "s0")
    assert exc.value.code == "ATTEST_INVALID"
    with pytest.raises(ac.AttestationError) as exc:
        add_root(store, "k-bad", "ca", "s0", not_before=200, not_after=100)
    assert exc.value.code == "ATTEST_INVALID"
    assert store.root_version() == 1


def test_verify_single_ring_anchored_to_root():
    store = verifier()
    add_root(store, "k-0", "ca", "s0")
    env = envelope("e-0", 0, "ca", "svc-a", "k-0", 100, "s0")
    verdict = store.verify([env], at=1000)
    assert verdict.ok is True
    assert verdict.break_report is None
    assert len(verdict.rings) == 1
    ring = verdict.rings[0]
    assert (ring.index, ring.envelope_id, ring.key_id) == (0, "e-0", "k-0")
    assert ring.ok is True
    assert ring.reason is None
    assert verdict.root_version == 1
    assert verdict.verified_at == 1000


def test_verify_chain_links_each_ring():
    store, chain = three_ring_setup()
    verdict = store.verify(chain, at=1000)
    assert verdict.ok is True
    assert [ring.index for ring in verdict.rings] == [0, 1, 2]
    assert [ring.key_id for ring in verdict.rings] == ["k-0", "k-1", "k-2"]
    assert [ring.subject for ring in verdict.rings] == ["svc-a", "svc-b", "svc-c"]
    assert all(ring.ok for ring in verdict.rings)


def test_link_mismatch_is_localized_to_ring():
    store = verifier()
    add_root(store, "k-0", "ca", "s0")
    add_root(store, "k-1", "svc-a", "s1")
    add_root(store, "k-x", "svc-x", "sx")
    chain = [
        envelope("e-0", 0, "ca", "svc-a", "k-0", 100, "s0"),
        envelope("e-1", 1, "svc-a", "svc-b", "k-1", 110, "s1"),
        envelope("e-2", 2, "svc-x", "svc-c", "k-x", 120, "sx"),
    ]
    verdict = store.verify(chain, at=1000)
    assert verdict.ok is False
    report = verdict.break_report
    assert (report.index, report.envelope_id, report.reason) == (
        2, "e-2", "ATTEST_LINK_BROKEN")
    assert len(verdict.rings) == 3
    assert verdict.rings[-1].reason == "ATTEST_LINK_BROKEN"


def test_bad_signature_is_localized_to_ring():
    store, chain = three_ring_setup()
    tampered = ac.Envelope(envelope_id="e-1", seq=1, issuer="svc-a",
                           subject="svc-b", key_id="k-1", signed_at=110,
                           payload_sha="payload-1", sig="0" * 64)
    verdict = store.verify([chain[0], tampered, chain[2]], at=1000)
    assert verdict.ok is False
    report = verdict.break_report
    assert (report.index, report.envelope_id, report.reason) == (
        1, "e-1", "ATTEST_BAD_SIGNATURE")
    assert len(verdict.rings) == 2


def test_unknown_key_is_localized_to_ring():
    store, chain = three_ring_setup()
    ghost = ac.Envelope(envelope_id="e-1", seq=1, issuer="svc-a",
                        subject="svc-b", key_id="ghost", signed_at=110,
                        payload_sha="payload-1", sig="1" * 64)
    verdict = store.verify([chain[0], ghost, chain[2]], at=1000)
    assert verdict.ok is False
    assert verdict.break_report.index == 1
    assert verdict.break_report.reason == "ATTEST_UNKNOWN_KEY"


def test_chain_length_cap_is_enforced():
    store, chain = three_ring_setup(max_chain=2)
    with pytest.raises(ac.AttestationError) as exc:
        store.verify(chain, at=1000)
    assert exc.value.code == "ATTEST_CHAIN_TOO_LONG"


def test_verify_envelope_returns_single_ring_result():
    store, chain = three_ring_setup()
    ring = store.verify_envelope(chain[1], at=1000)
    assert ring.ok is True
    assert ring.index == 0
    assert (ring.envelope_id, ring.key_id, ring.issuer, ring.subject) == (
        "e-1", "k-1", "svc-a", "svc-b")


def test_first_failure_wins_and_locate_break_is_pure():
    store = verifier()
    add_root(store, "k-0", "ca", "s0")
    add_root(store, "k-1", "svc-a", "s1")
    add_root(store, "k-x", "svc-x", "sx")
    tampered = ac.Envelope(envelope_id="e-1", seq=1, issuer="svc-a",
                           subject="svc-b", key_id="k-1", signed_at=110,
                           payload_sha="payload-1", sig="0" * 64)
    chain = [
        envelope("e-0", 0, "ca", "svc-a", "k-0", 100, "s0"),
        tampered,
        envelope("e-2", 2, "svc-x", "svc-c", "k-x", 120, "sx"),
    ]
    verdict = store.verify(chain, at=1000)
    assert verdict.break_report.reason == "ATTEST_BAD_SIGNATURE"
    assert verdict.break_report.index == 1
    before = store.stats()
    report = store.locate_break(chain, at=1000)
    assert report == verdict.break_report
    assert store.stats() == before


def test_rotation_new_key_can_sign_later_ring():
    store = verifier()
    add_root(store, "k-0", "ca", "s0", not_before=0, not_after=10000)
    store.rotate_key("k-0", "k-1", at=1000, secret="s1")
    assert store.root_version() == 2
    chain = [
        envelope("e-0", 0, "ca", "ca", "k-0", 900, "s0"),
        envelope("e-1", 1, "ca", "ca", "k-1", 1100, "s1"),
    ]
    verdict = store.verify(chain, at=1200)
    assert verdict.ok is True
    assert [ring.key_id for ring in verdict.rings] == ["k-0", "k-1"]
    assert store.root_version() == 2


def test_invalid_chain_input_is_rejected():
    store, chain = three_ring_setup()
    with pytest.raises(ac.AttestationError) as exc:
        store.verify([], at=1000)
    assert exc.value.code == "ATTEST_INVALID"
    with pytest.raises(ac.AttestationError) as exc:
        store.verify("not-a-chain", at=1000)
    assert exc.value.code == "ATTEST_INVALID"
    negative = ac.Envelope(envelope_id="e-x", seq=-1, issuer="ca",
                           subject="svc-a", key_id="k-0", signed_at=100,
                           payload_sha="payload-1", sig="0" * 64)
    with pytest.raises(ac.AttestationError) as exc:
        store.verify([negative], at=1000)
    assert exc.value.code == "ATTEST_INVALID"


def test_snapshot_restore_preserves_keys_and_history():
    store, chain = three_ring_setup()
    store.verify(chain, at=1000)
    bad = ac.Envelope(envelope_id="e-9", seq=0, issuer="ca", subject="svc-a",
                      key_id="k-0", signed_at=100, payload_sha="payload-1",
                      sig="0" * 64)
    store.verify([bad], at=1100)
    state = store.snapshot()
    assert set(state) == {"version", "keys", "root_version", "history", "stats"}
    pristine = copy.deepcopy(state)
    state["keys"].clear()
    assert store.active_keys(100) == ["k-0", "k-1", "k-2"]

    clone = verifier()
    clone.restore(pristine)
    assert clone.active_keys(100) == ["k-0", "k-1", "k-2"]
    assert clone.root_version() == store.root_version() == 3
    assert len(clone.history()) == 2
    assert clone.verify([chain[0]], at=1200).ok is True
    assert len(clone.history()) == 3

    with pytest.raises(ac.AttestationError) as exc:
        clone.restore({})
    assert exc.value.code == "ATTEST_SNAPSHOT_INVALID"
    assert len(clone.history()) == 3


def test_history_records_each_verification():
    store, chain = three_ring_setup()
    bad = ac.Envelope(envelope_id="e-0", seq=0, issuer="ca", subject="svc-a",
                      key_id="k-0", signed_at=100, payload_sha="payload-1",
                      sig="0" * 64)
    store.verify([chain[0]], at=1000)
    store.verify([chain[0]], at=1100)
    store.verify([bad], at=1200)
    recorded = store.history()
    assert [verdict.ok for verdict in recorded] == [True, True, False]
    assert [verdict.verified_at for verdict in recorded] == [1000, 1100, 1200]
    assert all(verdict.root_version == 3 for verdict in recorded)


def test_concurrent_verifications_are_consistent():
    store, chain = three_ring_setup()
    results = []
    errors = []
    gate = threading.Barrier(8)

    def worker():
        try:
            gate.wait(timeout=5)
            results.append(store.verify(chain, at=1000))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 8
    assert all(verdict.ok for verdict in results)
    assert store.stats() == {"verifications": 8, "verified": 8,
                             "rejected": 0, "rings": 24}
    assert len(store.history()) == 8
