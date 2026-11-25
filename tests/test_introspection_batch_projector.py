import copy

import pytest

import introspection_batch_projector as ip

ZERO_STATS = {
    "callers": 0,
    "tokens": 0,
    "batches": 0,
    "items": 0,
    "active": 0,
    "inactive": 0,
    "failed": 0,
    "fallbacks": 0,
    "grace_hits": 0,
    "projection_updates": 0,
}


def projector(**kwargs):
    return ip.IntrospectionBatchProjector(**kwargs)


def add_token(projector_obj, token="tok-1", *, token_type="access",
              client_id="cli-a", subject="alice", scope="read write",
              issued_at=0, expires_at=1000, grace_until=0, family_id=None,
              amr=()):
    return projector_obj.register_token(token, token_type=token_type,
                                        client_id=client_id, subject=subject,
                                        scope=scope, issued_at=issued_at,
                                        expires_at=expires_at,
                                        grace_until=grace_until,
                                        family_id=family_id, amr=amr)


def test_construction_and_caller_registration():
    for bad in (dict(max_items=0), dict(max_total_bytes=0), dict(max_token_length=0)):
        with pytest.raises(ip.IntrospectionError) as exc:
            projector(**bad)
        assert exc.value.code == "INTROSPECT_INVALID_REQUEST"

    proj = projector()
    assert proj.stats() == ZERO_STATS
    policy = proj.register_caller("audit")
    assert (policy.caller, policy.version, policy.fields) == ("audit", 1, ("sub", "scope"))
    assert proj.register_caller("audit") == policy
    assert proj.stats()["callers"] == 1
    with pytest.raises(ip.IntrospectionError) as exc:
        proj.register_caller("")
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    assert proj.stats()["callers"] == 1


def test_projection_policies_are_versioned():
    proj = projector()
    proj.register_caller("audit")
    updated = proj.set_projection("audit", ("sub", "ttl"), version=2)
    assert (updated.caller, updated.version, updated.fields) == ("audit", 2, ("sub", "ttl"))
    assert proj.set_projection("audit", ("sub", "ttl", "exp"), version=3).version == 3

    with pytest.raises(ip.IntrospectionError) as exc:
        proj.set_projection("audit", ("sub",), version=3)
    assert exc.value.code == "INTROSPECT_STALE_VERSION"
    with pytest.raises(ip.IntrospectionError) as exc:
        proj.set_projection("audit", ("sub",), version=1)
    assert exc.value.code == "INTROSPECT_STALE_VERSION"
    assert proj.projection_of("audit").fields == ("sub", "ttl", "exp")

    with pytest.raises(ip.IntrospectionError) as exc:
        proj.set_projection("ghost", ("sub",), version=1)
    assert exc.value.code == "INTROSPECT_UNKNOWN_CALLER"
    for fields in ((), ("sub", "nope"), ("sub", 5)):
        with pytest.raises(ip.IntrospectionError) as exc:
            proj.set_projection("audit", fields, version=9)
        assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    assert proj.projection_of("audit").version == 3
    assert proj.stats()["projection_updates"] == 2


def test_register_token_validates_fields_and_duplicates():
    proj = projector()
    record = add_token(proj)
    assert (record.token, record.token_type, record.subject) == ("tok-1", "access", "alice")
    assert record.amr == ()
    assert proj.token_of("tok-1") == record
    assert proj.token_of("missing") is None

    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "tok-1")
    assert exc.value.code == "INTROSPECT_DUPLICATE_TOKEN"
    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "")
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "tok-x", token_type="bogus")
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "tok-y", subject="   ")
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "tok-z", expires_at=0)
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    with pytest.raises(ip.IntrospectionError) as exc:
        add_token(proj, "tok-w", expires_at=1000, grace_until=500)
    assert exc.value.code == "INTROSPECT_INVALID_REQUEST"
    assert proj.stats()["tokens"] == 1


def test_single_active_token_projects_authorised_fields():
    proj = projector()
    proj.register_caller("audit")
    proj.set_projection("audit", ("token_type", "client_id", "sub", "scope", "exp", "ttl"),
                        version=2)
    add_token(proj)
    batch = proj.introspect_batch("audit", [{"token": "tok-1", "token_type_hint": "access"}],
                                  now=100)
    assert (batch.count, batch.failed, batch.bytes_used) == (1, 0, 5)
    row = batch.results[0]
    assert row["active"] is True
    assert (row["token_type"], row["client_id"]) == ("access", "cli-a")
    assert (row["sub"], row["scope"]) == ("alice", "read write")
    assert (row["exp"], row["ttl"]) == (1000, 900)
    assert proj.stats()["active"] == 1
    with pytest.raises(ip.IntrospectionError) as exc:
        proj.introspect_batch("ghost", ["tok-1"], now=100)
    assert exc.value.code == "INTROSPECT_UNKNOWN_CALLER"


def test_unknown_and_removed_tokens_report_inactive_only():
    proj = projector()
    proj.register_caller("audit")
    add_token(proj)
    batch = proj.introspect_batch("audit", ["ghost"], now=0)
    assert batch.results == ({"active": False},)
    assert batch.failed == 0
    assert proj.stats()["inactive"] == 1

    assert proj.remove_token("tok-1") is True
    assert proj.remove_token("tok-1") is False
    assert proj.token_of("tok-1") is None
    gone = proj.introspect_batch("audit", ["tok-1"], now=0)
    assert gone.results == ({"active": False},)


def test_batch_results_keep_request_order():
    proj = projector()
    proj.register_caller("audit")
    add_token(proj, "tok-a", subject="alice")
    add_token(proj, "tok-b", subject="bob")
    batch = proj.introspect_batch("audit", ["tok-b", "ghost", "tok-a"], now=0)
    assert (batch.count, batch.failed) == (3, 0)
    assert [row["active"] for row in batch.results] == [True, False, True]
    assert [row.get("sub") for row in batch.results] == ["bob", None, "alice"]
    assert proj.stats()["items"] == 3


def test_batch_size_and_byte_limits_are_enforced():
    proj = projector(max_items=2, max_total_bytes=20, max_token_length=8)
    proj.register_caller("audit")
    with pytest.raises(ip.IntrospectionError) as exc:
        proj.introspect_batch("audit", ["a", "b", "c"], now=0)
    assert exc.value.code == "INTROSPECT_BATCH_TOO_LARGE"
    with pytest.raises(ip.IntrospectionError) as exc:
        proj.introspect_batch("audit", ["1234567890", "1234567890", "1234567890"], now=0)
    assert exc.value.code == "INTROSPECT_BATCH_TOO_LARGE"

    at_limit = proj.introspect_batch("audit", ["12345678", "12345678"], now=0)
    assert at_limit.count == 2
    assert at_limit.bytes_used == 16

    empty = proj.introspect_batch("audit", [""], now=0)
    assert empty.results == ({"active": False, "error": "invalid_request"},)
    assert empty.failed == 1


def test_oversized_token_only_fails_its_own_slot():
    proj = projector(max_token_length=8)
    proj.register_caller("audit")
    batch = proj.introspect_batch("audit", ["0123456789"], now=0)
    assert batch.count == 1
    assert batch.failed == 1
    assert batch.bytes_used == 10
    assert batch.results == ({"active": False, "error": "token_too_long"},)
    assert proj.stats()["failed"] == 1
    assert proj.stats()["inactive"] == 0


def test_expired_token_in_grace_is_active_with_zero_ttl():
    proj = projector()
    proj.register_caller("audit")
    proj.set_projection("audit", ("sub", "exp", "ttl"), version=2)
    add_token(proj, "tok-g", expires_at=1000, grace_until=2000)

    live = proj.introspect_batch("audit", ["tok-g"], now=999).results[0]
    assert (live["active"], live["ttl"]) == (True, 1)
    grace = proj.introspect_batch("audit", ["tok-g"], now=1500).results[0]
    assert (grace["active"], grace["ttl"], grace["exp"]) == (True, 0, 1000)
    assert proj.stats()["grace_hits"] == 1
    dead = proj.introspect_batch("audit", ["tok-g"], now=2000).results[0]
    assert dead == {"active": False}
    assert proj.stats()["grace_hits"] == 1


def test_unusable_hint_falls_back_to_all_token_types():
    proj = projector()
    proj.register_caller("audit")
    add_token(proj, "tok-r", token_type="refresh", subject="carol", family_id="fam-1")
    batch = proj.introspect_batch(
        "audit", [{"token": "tok-r", "token_type_hint": "bogus"}], now=0)
    row = batch.results[0]
    assert row["active"] is True
    assert row["sub"] == "carol"
    assert proj.stats()["fallbacks"] == 1


def test_matching_hint_does_not_count_as_fallback():
    proj = projector()
    proj.register_caller("audit")
    proj.set_projection("audit", ("token_type", "sub"), version=2)
    add_token(proj, "tok-a", token_type="access")
    add_token(proj, "tok-r", token_type="refresh", subject="carol")
    batch = proj.introspect_batch(
        "audit", [{"token": "tok-r", "token_type_hint": "refresh"}], now=0)
    assert batch.results[0]["token_type"] == "refresh"
    assert batch.results[0]["sub"] == "carol"
    assert proj.stats()["fallbacks"] == 0


def test_each_caller_sees_its_own_projection():
    proj = projector()
    proj.register_caller("narrow")
    proj.register_caller("wide")
    proj.set_projection("narrow", ("sub",), version=2)
    proj.set_projection("wide", ("scope", "token_type", "client_id"), version=2)
    add_token(proj)

    narrow = proj.introspect_batch("narrow", ["tok-1"], now=0).results[0]
    wide = proj.introspect_batch("wide", ["tok-1"], now=0).results[0]
    assert narrow["sub"] == "alice"
    assert (wide["scope"], wide["token_type"], wide["client_id"]) == (
        "read write", "access", "cli-a")
    assert proj.projection_of("narrow").fields == ("sub",)
    assert proj.projection_of("wide").version == 2


def test_stats_track_batches_items_and_projection():
    proj = projector()
    proj.register_caller("audit")
    proj.set_projection("audit", ("sub", "ttl"), version=2)
    add_token(proj, "tok-1", expires_at=1000, grace_until=2000)
    add_token(proj, "tok-2", expires_at=500)

    batch = proj.introspect_batch("audit", ["tok-1", "ghost", "tok-2"], now=1500)
    assert batch.count == 3
    assert [row["active"] for row in batch.results] == [True, False, False]
    over = proj.introspect_batch("audit", ["x" * 600], now=0)
    assert over.failed == 1
    assert proj.stats() == {
        "callers": 1,
        "tokens": 2,
        "batches": 2,
        "items": 4,
        "active": 1,
        "inactive": 2,
        "failed": 1,
        "fallbacks": 0,
        "grace_hits": 1,
        "projection_updates": 1,
    }


def test_snapshot_is_detached_and_restore_roundtrips():
    proj = projector()
    proj.register_caller("audit")
    proj.set_projection("audit", ("sub", "ttl"), version=2)
    add_token(proj, "tok-1", expires_at=1000)

    state = proj.snapshot()
    assert set(state) == {"version", "tokens", "callers", "policies", "stats"}
    pristine = copy.deepcopy(state)
    state["tokens"].clear()
    state["stats"]["tokens"] = 99
    assert proj.stats()["tokens"] == 1
    assert proj.projection_of("audit").fields == ("sub", "ttl")
    assert proj.token_of("tok-1").subject == "alice"

    clone = projector()
    clone.restore(pristine)
    assert clone.stats()["tokens"] == 1
    assert clone.projection_of("audit").version == 2
    assert clone.token_of("tok-1").subject == "alice"
    out = clone.introspect_batch("audit", ["tok-1"], now=100)
    assert out.results[0]["ttl"] == 900


def test_restore_rejects_malformed_state_and_keeps_live_data():
    proj = projector()
    proj.register_caller("audit")
    add_token(proj)
    good = proj.snapshot()
    without_version = {key: value for key, value in good.items() if key != "version"}

    bad_states = [
        None,
        {},
        {"version": 2, **without_version},
        {**good, "tokens": {}},
        {**good, "callers": "nope"},
        {**good, "policies": [{"caller": "audit"}]},
        {**good, "stats": {"tokens": 1}},
        {**good, "tokens": [{"token": "x"}]},
    ]
    for bad in bad_states:
        with pytest.raises(ip.IntrospectionError) as exc:
            proj.restore(bad)
        assert exc.value.code == "INTROSPECT_SNAPSHOT_INVALID"

    assert proj.stats()["tokens"] == 1
    assert proj.projection_of("audit").fields == ("sub", "scope")
    out = proj.introspect_batch("audit", ["tok-1"], now=0)
    assert out.results[0]["active"] is True
