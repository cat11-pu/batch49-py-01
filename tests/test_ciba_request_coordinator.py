import copy

import pytest

import ciba_request_coordinator as cb

ZERO_STATS = {
    "users": 0,
    "created": 0,
    "hints_routed": 0,
    "hints_undelivered": 0,
    "approved": 0,
    "denied": 0,
    "polls": 0,
    "slow_downs": 0,
    "delivered": 0,
    "expired": 0,
}


def coord(**kwargs):
    return cb.CibaRequestCoordinator(**kwargs)


def new_request(coordinator, *, client="c1", user="u1", now=0, ttl_ms=600000,
                interval_ms=1000, scope="openid", binding_message="42",
                hint="push"):
    return coordinator.create_request(client, user, scope=scope,
                                      binding_message=binding_message, hint=hint,
                                      now=now, ttl_ms=ttl_ms, interval_ms=interval_ms)


def test_construction_and_device_registration_validation():
    with pytest.raises(cb.CibaError) as exc:
        coord(per_user_limit=0)
    assert exc.value.code == "CIBA_INVALID_REQUEST"
    with pytest.raises(cb.CibaError) as exc:
        coord(per_client_limit=-1)
    assert exc.value.code == "CIBA_INVALID_REQUEST"
    with pytest.raises(cb.CibaError) as exc:
        coord(max_interval_ms=0)
    assert exc.value.code == "CIBA_INVALID_REQUEST"

    coordinator = coord()
    assert coordinator.stats() == ZERO_STATS
    coordinator.register_device("u1", "d1")
    coordinator.register_device("u1", "d1")
    coordinator.register_device("u1", "d2")
    assert coordinator.stats()["users"] == 1
    with pytest.raises(cb.CibaError) as exc:
        coordinator.register_device("", "d3")
    assert exc.value.code == "CIBA_INVALID_REQUEST"
    with pytest.raises(cb.CibaError) as exc:
        coordinator.register_device("u1", "  ")
    assert exc.value.code == "CIBA_INVALID_REQUEST"


def test_create_request_binds_fields_and_uses_unique_ids():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=1000, ttl_ms=60000, interval_ms=5000,
                          scope="openid profile", binding_message="99 42", hint="login")
    assert (request.client, request.user) == ("c1", "u1")
    assert (request.scope, request.binding_message, request.hint) == ("openid profile", "99 42", "login")
    assert (request.status, request.interval_ms) == ("pending", 5000)
    assert (request.created_at, request.expires_at) == (1000, 61000)
    assert (request.approved_by, request.denied_reason) == (None, None)
    assert request.auth_req_id
    assert coordinator.request_of(request.auth_req_id) == request

    other = new_request(coordinator, now=1000)
    assert other.auth_req_id != request.auth_req_id
    assert coordinator.stats()["created"] == 2

    bad_calls = [
        dict(client="", user="u1", scope="openid"),
        dict(client="c1", user="", scope="openid"),
        dict(client="c1", user="u1", scope=""),
        dict(client="c1", user="u1", scope="openid", binding_message=""),
        dict(client="c1", user="u1", scope="openid", hint=""),
    ]
    for bad in bad_calls:
        with pytest.raises(cb.CibaError) as exc:
            coordinator.create_request(bad["client"], bad["user"],
                                       scope=bad["scope"],
                                       binding_message=bad.get("binding_message", "42"),
                                       hint=bad.get("hint", "push"), now=0)
        assert exc.value.code == "CIBA_INVALID_REQUEST"


def test_open_request_limits_per_user_and_per_client():
    by_user = coord(per_user_limit=2, per_client_limit=10)
    by_user.register_device("u1", "d1")
    for index in range(2):
        new_request(by_user, client="c1", now=0, binding_message=str(index))
    assert by_user.open_count("u1") == 2
    with pytest.raises(cb.CibaError) as exc:
        new_request(by_user, client="c2", now=0)
    assert exc.value.code == "CIBA_LIMIT_EXCEEDED"
    assert by_user.open_count("u1") == 2

    assert by_user.expire(10 ** 9) == 2
    assert by_user.open_count("u1") == 0
    fresh = new_request(by_user, client="c2", now=10 ** 9)
    assert by_user.request_of(fresh.auth_req_id).status == "pending"

    by_client = coord(per_user_limit=10, per_client_limit=2)
    for user in ("u1", "u2"):
        by_client.register_device(user, "d-" + user)
        new_request(by_client, client="cli", user=user, now=0)
    assert by_client.open_count(client="cli") == 2
    with pytest.raises(cb.CibaError) as exc:
        new_request(by_client, client="cli", user="u3", now=0)
    assert exc.value.code == "CIBA_LIMIT_EXCEEDED"


def test_route_hint_targets_sorted_devices_once():
    coordinator = coord()
    coordinator.register_device("u1", "d2")
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    assert coordinator.route_hint(request.auth_req_id, now=0) == ("d1", "d2")
    assert coordinator.route_hint(request.auth_req_id, now=5) == ("d1", "d2")
    assert coordinator.stats()["hints_routed"] == 1

    with pytest.raises(cb.CibaError) as exc:
        coordinator.route_hint("missing", now=0)
    assert exc.value.code == "CIBA_UNKNOWN_REQUEST"

    lonely = new_request(coordinator, user="u2", now=0)
    assert coordinator.route_hint(lonely.auth_req_id, now=0) == ()
    assert coordinator.stats()["hints_undelivered"] == 1


def test_approve_records_device_and_cannot_repeat():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    approved = coordinator.approve(request.auth_req_id, "d1", now=10)
    assert (approved.status, approved.approved_by) == ("approved", "d1")
    assert coordinator.request_of(request.auth_req_id).status == "approved"
    assert coordinator.stats()["approved"] == 1
    with pytest.raises(cb.CibaError) as exc:
        coordinator.approve(request.auth_req_id, "d1", now=20)
    assert exc.value.code == "CIBA_REQUEST_NOT_PENDING"


def test_deny_records_reason_and_settles_the_request():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    denied = coordinator.deny(request.auth_req_id, "d1", reason="not me", now=10)
    assert (denied.status, denied.denied_reason) == ("denied", "not me")
    assert coordinator.stats()["denied"] == 1
    with pytest.raises(cb.CibaError) as exc:
        coordinator.approve(request.auth_req_id, "d1", now=20)
    assert exc.value.code == "CIBA_REQUEST_NOT_PENDING"

    second = new_request(coordinator, now=0)
    with pytest.raises(cb.CibaError) as exc:
        coordinator.deny(second.auth_req_id, "d1", reason="  ", now=10)
    assert exc.value.code == "CIBA_INVALID_REQUEST"
    assert coordinator.request_of(second.auth_req_id).status == "pending"


def test_early_polls_slow_down_and_double_the_interval():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0, interval_ms=1000)
    first = coordinator.poll(request.auth_req_id, client="c1", now=0)
    assert (first.status, first.interval_ms) == ("slow_down", 2000)
    second = coordinator.poll(request.auth_req_id, client="c1", now=0)
    assert (second.status, second.interval_ms) == ("slow_down", 4000)
    third = coordinator.poll(request.auth_req_id, client="c1", now=0)
    assert third.interval_ms == 8000
    on_time = coordinator.poll(request.auth_req_id, client="c1", now=8000)
    assert (on_time.status, on_time.interval_ms) == ("pending", 8000)
    assert coordinator.stats()["slow_downs"] == 3

    capped = coord(max_interval_ms=2000)
    capped.register_device("u1", "d1")
    other = new_request(capped, now=0, interval_ms=1000)
    assert capped.poll(other.auth_req_id, client="c1", now=0).interval_ms == 2000
    assert capped.poll(other.auth_req_id, client="c1", now=0).interval_ms == 2000


def test_poll_delivers_approved_token_exactly_once():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0, interval_ms=1000)
    coordinator.approve(request.auth_req_id, "d1", now=0)
    first = coordinator.poll(request.auth_req_id, client="c1", now=1000)
    assert first.status == "approved"
    assert first.token
    assert first.reason == ""
    assert coordinator.stats()["delivered"] == 1
    second = coordinator.poll(request.auth_req_id, client="c1", now=2000)
    assert (second.status, second.token) == ("consumed", None)
    assert coordinator.request_of(request.auth_req_id).status == "consumed"


def test_poll_cadence_is_tracked_per_request():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    first = new_request(coordinator, now=0, interval_ms=1000)
    second = new_request(coordinator, now=0, interval_ms=1000)
    assert coordinator.poll(first.auth_req_id, client="c1", now=0).status == "slow_down"
    assert coordinator.poll(second.auth_req_id, client="c1", now=1000).status == "pending"
    assert coordinator.poll(first.auth_req_id, client="c1", now=1000).status == "slow_down"
    assert coordinator.poll(first.auth_req_id, client="c1", now=5000).status == "pending"
    assert coordinator.poll(second.auth_req_id, client="c1", now=3000).status == "pending"
    assert coordinator.request_of(first.auth_req_id).status == "pending"
    assert coordinator.request_of(second.auth_req_id).status == "pending"


def test_poll_after_denial_returns_the_reason():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    coordinator.deny(request.auth_req_id, "d1", reason="wrong person", now=0)
    result = coordinator.poll(request.auth_req_id, client="c1", now=1000)
    assert (result.status, result.reason, result.token) == ("denied", "wrong person", None)
    assert coordinator.request_of(request.auth_req_id).status == "denied"


def test_poll_rejects_unknown_ids_and_foreign_clients():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    with pytest.raises(cb.CibaError) as exc:
        coordinator.poll("missing", client="c1", now=0)
    assert exc.value.code == "CIBA_UNKNOWN_REQUEST"
    with pytest.raises(cb.CibaError) as exc:
        coordinator.poll(request.auth_req_id, client="c2", now=0)
    assert exc.value.code == "CIBA_CLIENT_MISMATCH"
    assert coordinator.request_of(request.auth_req_id).status == "pending"
    assert coordinator.stats()["polls"] == 0


def test_expire_is_bounded_and_blocks_late_approval():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0, ttl_ms=1000)
    assert coordinator.expire(999) == 0
    assert coordinator.expire(1000) == 1
    assert coordinator.request_of(request.auth_req_id).status == "expired"
    assert coordinator.expire(1000) == 0
    with pytest.raises(cb.CibaError) as exc:
        coordinator.approve(request.auth_req_id, "d1", now=1000)
    assert exc.value.code == "CIBA_REQUEST_EXPIRED"
    assert coordinator.stats()["expired"] == 1


def test_stats_track_the_whole_lifecycle():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    approved = new_request(coordinator, now=0, interval_ms=1000)
    denied = new_request(coordinator, now=0, interval_ms=1000)
    coordinator.route_hint(approved.auth_req_id, now=0)
    coordinator.approve(approved.auth_req_id, "d1", now=0)
    assert coordinator.poll(approved.auth_req_id, client="c1", now=1000).status == "approved"
    assert coordinator.poll(approved.auth_req_id, client="c1", now=5000).status == "consumed"
    coordinator.deny(denied.auth_req_id, "d1", reason="no", now=0)
    assert coordinator.poll(denied.auth_req_id, client="c1", now=1000).status == "denied"
    assert coordinator.expire(10 ** 9) == 1
    assert coordinator.stats() == {
        "users": 1,
        "created": 2,
        "hints_routed": 1,
        "hints_undelivered": 0,
        "approved": 1,
        "denied": 1,
        "polls": 3,
        "slow_downs": 0,
        "delivered": 1,
        "expired": 1,
    }


def test_snapshot_is_detached_and_restore_roundtrips():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0, interval_ms=1000)
    coordinator.route_hint(request.auth_req_id, now=0)
    coordinator.approve(request.auth_req_id, "d1", now=0)

    state = coordinator.snapshot()
    assert set(state) == {"version", "devices", "requests", "stats"}
    pristine = copy.deepcopy(state)
    state["requests"].clear()
    state["stats"]["approved"] = 99
    assert coordinator.stats()["approved"] == 1
    assert coordinator.request_of(request.auth_req_id).status == "approved"

    clone = coord()
    clone.restore(pristine)
    assert clone.stats()["approved"] == 1
    assert clone.request_of(request.auth_req_id).status == "approved"
    assert clone.route_hint(request.auth_req_id, now=0) == ("d1",)
    delivered = clone.poll(request.auth_req_id, client="c1", now=1000)
    assert delivered.status == "approved"
    assert delivered.token


def test_restore_rejects_malformed_state_and_keeps_live_data():
    coordinator = coord()
    coordinator.register_device("u1", "d1")
    request = new_request(coordinator, now=0)
    assert coordinator.approve(request.auth_req_id, "d1", now=0).status == "approved"
    good = coordinator.snapshot()
    without_version = {key: value for key, value in good.items() if key != "version"}

    bad_states = [
        None,
        {},
        {"version": 2, **without_version},
        {**good, "devices": []},
        {**good, "requests": {}},
        {**good, "stats": {"approved": 1}},
        {**good, "requests": [{"auth_req_id": "x"}]},
        {**good, "version": "one"},
    ]
    for bad in bad_states:
        with pytest.raises(cb.CibaError) as exc:
            coordinator.restore(bad)
        assert exc.value.code == "CIBA_SNAPSHOT_INVALID"

    assert coordinator.stats()["approved"] == 1
    assert coordinator.request_of(request.auth_req_id).status == "approved"
    assert coordinator.poll(request.auth_req_id, client="c1", now=1000).status == "approved"
