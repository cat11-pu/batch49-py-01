import copy
import threading

import pytest

import dpop_nonce_issuer as dp

ZERO_STATS = {
    "clients": 0,
    "nonces_issued": 0,
    "nonces_evicted": 0,
    "nonces_expired": 0,
    "verifies": 0,
    "verified": 0,
    "challenged": 0,
    "replays": 0,
    "key_mismatch": 0,
    "migrations": 0,
}


def make_issuer(**kwargs):
    return dp.DpopNonceIssuer(**kwargs)


def setup_token(issuer, client="shop", token_id="tok-1", jkt="jkt-a", now=0):
    issuer.register_client(client)
    return issuer.bind_token(client, token_id, jkt=jkt, now=now)


def test_construction_and_registration_validation():
    with pytest.raises(dp.DpopError) as exc:
        make_issuer(nonce_capacity=0)
    assert exc.value.code == "DPOP_INVALID_REQUEST"
    with pytest.raises(dp.DpopError) as exc:
        make_issuer(nonce_ttl_ms=0)
    assert exc.value.code == "DPOP_INVALID_REQUEST"
    with pytest.raises(dp.DpopError) as exc:
        make_issuer(migration_grace_ms=-1)
    assert exc.value.code == "DPOP_INVALID_REQUEST"

    issuer = make_issuer()
    assert issuer.stats() == ZERO_STATS
    assert issuer.register_client("shop") == 1
    assert issuer.register_client("shop") == 1
    assert issuer.stats()["clients"] == 1
    with pytest.raises(dp.DpopError) as exc:
        issuer.register_client("")
    assert exc.value.code == "DPOP_INVALID_REQUEST"


def test_bind_token_records_binding_and_refuses_duplicates():
    issuer = make_issuer()
    issuer.register_client("shop")
    binding = issuer.bind_token("shop", "tok-1", jkt="jkt-a", now=5)
    assert (binding.token_id, binding.client, binding.current_jkt) == ("tok-1", "shop", "jkt-a")
    assert (binding.previous_jkt, binding.grace_until, binding.bound_at) == (None, None, 5)
    assert issuer.binding_of("tok-1") == binding
    assert issuer.binding_of("tok-2") is None
    with pytest.raises(dp.DpopError) as exc:
        issuer.bind_token("shop", "tok-1", jkt="jkt-b", now=6)
    assert exc.value.code == "DPOP_TOKEN_ALREADY_BOUND"
    assert issuer.binding_of("tok-1").current_jkt == "jkt-a"


def test_issue_nonce_stamps_client_generation_and_expiry():
    issuer = make_issuer(nonce_ttl_ms=5000)
    issuer.register_client("shop")
    nonce = issuer.issue_nonce("shop", now=100)
    assert (nonce.client, nonce.generation, nonce.expires_at) == ("shop", 1, 5100)
    assert nonce.consumed is False
    assert issuer.nonce_of(nonce.value) == nonce
    assert issuer.stats()["nonces_issued"] == 1
    with pytest.raises(dp.DpopError) as exc:
        issuer.issue_nonce("ghost", now=100)
    assert exc.value.code == "DPOP_UNKNOWN_CLIENT"


def test_proof_ok_consumes_nonce_and_second_use_is_replay():
    issuer = make_issuer()
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    result = issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt="jkt-a", now=0)
    assert (result.status, result.nonce, result.generation) == ("ok", None, 1)
    assert issuer.nonce_of(nonce.value).consumed is True
    again = issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt="jkt-a", now=0)
    assert (again.status, again.nonce, again.generation) == ("replay", None, None)
    assert issuer.stats()["verified"] == 1
    assert issuer.stats()["replays"] == 1


def test_key_mismatch_does_not_consume_the_nonce():
    issuer = make_issuer()
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    mismatch = issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt="jkt-x", now=0)
    assert (mismatch.status, mismatch.nonce) == ("key_mismatch", None)
    assert issuer.nonce_of(nonce.value).consumed is False
    assert issuer.verify_proof("shop", "tok-1", nonce=nonce.value,
                               jkt="jkt-a", now=0).status == "ok"


def test_unknown_token_and_unknown_client_paths():
    issuer = make_issuer()
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    missing = issuer.verify_proof("shop", "tok-404", nonce=nonce.value, jkt="jkt-a", now=0)
    assert (missing.status, missing.nonce) == ("unknown_token", None)
    assert issuer.nonce_of(nonce.value).consumed is False
    with pytest.raises(dp.DpopError) as exc:
        issuer.verify_proof("ghost", "tok-1", nonce=nonce.value, jkt="jkt-a", now=0)
    assert exc.value.code == "DPOP_UNKNOWN_CLIENT"


def test_foreign_nonce_is_refused_without_consuming_it():
    issuer = make_issuer()
    setup_token(issuer, client="shop", token_id="tok-shop", jkt="jkt-a")
    setup_token(issuer, client="bank", token_id="tok-bank", jkt="jkt-b")
    nonce = issuer.issue_nonce("shop", now=0)
    result = issuer.verify_proof("bank", "tok-bank", nonce=nonce.value, jkt="jkt-b", now=0)
    assert (result.status, result.generation) == ("foreign_nonce", None)
    assert issuer.nonce_of(nonce.value).consumed is False
    assert issuer.verify_proof("shop", "tok-shop", nonce=nonce.value,
                               jkt="jkt-a", now=0).status == "ok"


def test_expired_nonce_is_refused_and_expire_drops_it():
    issuer = make_issuer(nonce_ttl_ms=1000)
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    result = issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt="jkt-a", now=1000)
    assert (result.status, result.nonce) == ("expired_nonce", None)
    assert issuer.stats()["nonces_expired"] == 0
    assert issuer.expire(1000) == 1
    assert issuer.nonce_of(nonce.value) is None
    assert issuer.stats()["nonces_expired"] == 1
    assert issuer.verify_proof("shop", "tok-1", nonce=nonce.value,
                               jkt="jkt-a", now=1000).status == "unknown_nonce"


def test_invalid_arguments_are_rejected():
    issuer = make_issuer()
    issuer.register_client("shop")
    with pytest.raises(dp.DpopError) as exc:
        issuer.bind_token("shop", "", jkt="jkt-a", now=0)
    assert exc.value.code == "DPOP_INVALID_REQUEST"
    with pytest.raises(dp.DpopError) as exc:
        issuer.bind_token("shop", "tok-1", jkt="   ", now=0)
    assert exc.value.code == "DPOP_INVALID_REQUEST"
    with pytest.raises(dp.DpopError) as exc:
        issuer.bind_token("ghost", "tok-1", jkt="jkt-a", now=0)
    assert exc.value.code == "DPOP_UNKNOWN_CLIENT"
    assert issuer.binding_of("tok-1") is None


def test_migrate_key_allows_both_keys_during_grace():
    issuer = make_issuer(migration_grace_ms=1000)
    setup_token(issuer, jkt="jkt-old")
    moved = issuer.migrate_key("shop", "tok-1", new_jkt="jkt-new", now=10)
    assert (moved.current_jkt, moved.previous_jkt) == ("jkt-new", "jkt-old")
    assert moved.grace_until == 1010
    assert issuer.binding_of("tok-1") == moved
    assert issuer.stats()["migrations"] == 1

    for key in ("jkt-new", "jkt-old"):
        nonce = issuer.issue_nonce("shop", now=20)
        result = issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt=key, now=20)
        assert result.status == "ok"
    assert issuer.stats()["verified"] == 2


def test_rotate_generation_stamps_later_nonces():
    issuer = make_issuer()
    issuer.register_client("shop")
    assert issuer.issue_nonce("shop", now=0).generation == 1
    assert issuer.rotate_generation("shop", now=0) == 2
    assert issuer.issue_nonce("shop", now=0).generation == 2
    assert issuer.rotate_generation("shop", now=0) == 3
    assert issuer.stats()["nonces_issued"] == 2
    assert issuer.stats()["challenged"] == 0
    with pytest.raises(dp.DpopError) as exc:
        issuer.rotate_generation("ghost", now=0)
    assert exc.value.code == "DPOP_UNKNOWN_CLIENT"


def test_concurrent_proofs_consume_nonce_exactly_once():
    issuer = make_issuer()
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    results = []
    errors = []
    barrier = threading.Barrier(6)

    def worker():
        try:
            barrier.wait(timeout=5)
            results.append(issuer.verify_proof("shop", "tok-1", nonce=nonce.value,
                                               jkt="jkt-a", now=0))
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(row.status for row in results) == ["ok"] + ["replay"] * 5
    assert issuer.stats()["verified"] == 1
    assert issuer.stats()["replays"] == 5


def test_stats_track_nonce_and_proof_events():
    issuer = make_issuer()
    setup_token(issuer)
    first = issuer.issue_nonce("shop", now=0)
    second = issuer.issue_nonce("shop", now=0)
    assert issuer.verify_proof("shop", "tok-1", nonce=first.value, jkt="jkt-a", now=0).status == "ok"
    assert issuer.verify_proof("shop", "tok-1", nonce=first.value, jkt="jkt-a", now=0).status == "replay"
    assert issuer.verify_proof("shop", "tok-1", nonce=second.value, jkt="jkt-b", now=0).status == "key_mismatch"
    assert issuer.verify_proof("shop", "tok-1", nonce=second.value, jkt="jkt-a", now=0).status == "ok"

    assert issuer.expire(100000) == 2
    assert issuer.stats() == {
        "clients": 1,
        "nonces_issued": 2,
        "nonces_evicted": 0,
        "nonces_expired": 2,
        "verifies": 4,
        "verified": 2,
        "challenged": 0,
        "replays": 1,
        "key_mismatch": 1,
        "migrations": 0,
    }


def test_snapshot_is_detached_and_restore_roundtrips():
    issuer = make_issuer(nonce_capacity=8)
    setup_token(issuer)
    used = issuer.issue_nonce("shop", now=0)
    assert issuer.verify_proof("shop", "tok-1", nonce=used.value, jkt="jkt-a", now=0).status == "ok"
    issuer.migrate_key("shop", "tok-1", new_jkt="jkt-b", now=10)

    state = issuer.snapshot()
    assert set(state) == {"version", "clients", "bindings", "nonces", "stats"}
    pristine = copy.deepcopy(state)
    state["bindings"].clear()
    state["stats"]["verified"] = 99
    assert issuer.stats()["verified"] == 1
    assert issuer.binding_of("tok-1").current_jkt == "jkt-b"
    assert issuer.nonce_of(used.value).consumed is True

    clone = make_issuer(nonce_capacity=8)
    clone.restore(pristine)
    assert clone.stats()["verified"] == 1
    assert clone.binding_of("tok-1").current_jkt == "jkt-b"
    assert clone.nonce_of(used.value).consumed is True
    fresh = clone.issue_nonce("shop", now=20)
    assert clone.verify_proof("shop", "tok-1", nonce=fresh.value,
                              jkt="jkt-a", now=20).status == "ok"


def test_restore_rejects_malformed_state_and_keeps_live_data():
    issuer = make_issuer()
    setup_token(issuer)
    nonce = issuer.issue_nonce("shop", now=0)
    assert issuer.verify_proof("shop", "tok-1", nonce=nonce.value, jkt="jkt-a", now=0).status == "ok"
    good = issuer.snapshot()
    without_version = {key: value for key, value in good.items() if key != "version"}

    bad_states = [
        None,
        {},
        {"version": 2, **without_version},
        {**good, "clients": []},
        {**good, "bindings": {}},
        {**good, "nonces": "nope"},
        {**good, "stats": {"verified": 1}},
        {**good, "nonces": [{"value": "x"}]},
    ]
    for bad in bad_states:
        with pytest.raises(dp.DpopError) as exc:
            issuer.restore(bad)
        assert exc.value.code == "DPOP_SNAPSHOT_INVALID"

    assert issuer.stats()["verified"] == 1
    assert issuer.binding_of("tok-1").current_jkt == "jkt-a"
    assert issuer.nonce_of(nonce.value).consumed is True
    fresh = issuer.issue_nonce("shop", now=10)
    assert issuer.verify_proof("shop", "tok-1", nonce=fresh.value,
                               jkt="jkt-a", now=10).status == "ok"
