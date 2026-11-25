import pytest

import ws_subscription_authorizer as wa

NOW = "2026-11-25T10:00:00+00:00"


def authorizer(**kwargs):
    return wa.WsSubscriptionAuthorizer(**kwargs)


def subscriber(reg, conn_id, topic, now=NOW):
    reg.connect(conn_id)
    reg.grant(conn_id, topic)
    return reg.subscribe(conn_id, topic, now=now)


def test_connect_registers_connection_and_stats():
    reg = authorizer()
    reg.connect("c1")
    reg.connect("c2")
    assert reg.stats() == {"connections": 2, "subscriptions": 0, "grants": 0,
                           "outbox_items": 0, "broadcast_delivered": 0,
                           "broadcast_dropped": 0, "revoked_subscriptions": 0}
    assert reg.disconnect("c2") == 0
    assert reg.stats()["connections"] == 1


def test_connect_is_idempotent_per_conn_id():
    reg = authorizer()
    reg.connect("c1")
    reg.connect("c1")
    assert reg.stats()["connections"] == 1
    assert reg.disconnect("c1") == 0
    assert reg.stats()["connections"] == 0
    assert reg.disconnect("c1") == 0


def test_connection_limit_is_enforced():
    reg = authorizer(max_connections=1)
    reg.connect("c1")
    with pytest.raises(wa.WsAuthError) as exc:
        reg.connect("c2")
    assert exc.value.code == "WS_CONNECTION_LIMIT"
    assert reg.stats()["connections"] == 1


def test_subscribe_requires_topic_grant():
    reg = authorizer()
    reg.connect("c1")
    with pytest.raises(wa.WsAuthError) as exc:
        reg.subscribe("c1", "news", now=NOW)
    assert exc.value.code == "WS_TOPIC_NOT_ALLOWED"
    assert reg.stats()["subscriptions"] == 0


def test_unknown_connection_is_reported():
    reg = authorizer()
    with pytest.raises(wa.WsAuthError) as exc:
        reg.grant("ghost", "news")
    assert exc.value.code == "WS_UNKNOWN_CONNECTION"
    with pytest.raises(wa.WsAuthError) as exc:
        reg.subscribe("ghost", "news", now=NOW)
    assert exc.value.code == "WS_UNKNOWN_CONNECTION"
    assert reg.unsubscribe("ghost", "news") is False


def test_subscribe_returns_view_and_counts_grants():
    reg = authorizer()
    reg.connect("c1")
    reg.grant("c1", "news")
    reg.grant("c1", "prices")
    view = reg.subscribe("c1", "news", now=NOW)
    assert (view.conn_id, view.topic, view.subscribed_at) == ("c1", "news", NOW)
    reg.subscribe("c1", "prices", now=NOW)
    assert reg.stats()["subscriptions"] == 2
    assert reg.stats()["grants"] == 2


def test_subscription_limit_per_connection():
    reg = authorizer(max_topics_per_conn=2)
    reg.connect("c1")
    for topic in ("a", "b", "c"):
        reg.grant("c1", topic)
    reg.subscribe("c1", "a", now=NOW)
    reg.subscribe("c1", "b", now=NOW)
    with pytest.raises(wa.WsAuthError) as exc:
        reg.subscribe("c1", "c", now=NOW)
    assert exc.value.code == "WS_SUBSCRIPTION_LIMIT"
    assert reg.unsubscribe("c1", "b") is True
    reg.subscribe("c1", "c", now=NOW)
    assert reg.stats()["subscriptions"] == 2


def test_unsubscribe_reports_whether_topic_was_present():
    reg = authorizer()
    subscriber(reg, "c1", "news")
    assert reg.unsubscribe("c1", "news") is True
    assert reg.unsubscribe("c1", "news") is False
    assert reg.stats()["subscriptions"] == 0
    assert reg.stats()["grants"] == 1


def test_broadcast_reaches_only_topic_subscribers():
    reg = authorizer()
    subscriber(reg, "c1", "news")
    subscriber(reg, "c2", "sports")
    reg.connect("c3")
    assert reg.broadcast("news", {"id": 7}, now=NOW) == 1
    assert [item.payload for item in reg.flush_outbox("c1")] == [{"id": 7}]
    assert reg.flush_outbox("c2") == []
    assert reg.flush_outbox("c3") == []
    assert reg.stats()["broadcast_delivered"] == 1


def test_broadcast_without_subscribers_is_a_noop():
    reg = authorizer()
    reg.connect("c1")
    assert reg.broadcast("nobody-here", "hello", now=NOW) == 0
    assert reg.stats()["broadcast_delivered"] == 0
    assert reg.stats()["outbox_items"] == 0
    assert reg.flush_outbox("c1") == []


def test_flush_outbox_is_fifo_and_drains():
    reg = authorizer()
    subscriber(reg, "c1", "news")
    reg.broadcast("news", "m1", now=NOW)
    reg.broadcast("news", "m2", now=NOW)
    items = reg.flush_outbox("c1")
    assert [item.seq for item in items] == [1, 2]
    assert [item.kind for item in items] == ["message", "message"]
    assert [item.payload for item in items] == ["m1", "m2"]
    assert reg.flush_outbox("c1") == []
    assert reg.stats()["outbox_items"] == 0


def test_revoke_grant_drops_subscription_and_queues_notice():
    reg = authorizer()
    subscriber(reg, "c1", "news")
    result = reg.revoke_grant("news", now=NOW)
    assert result == {"revoked_connections": 1, "removed_subscriptions": 1,
                      "notices": 1}
    assert reg.stats()["subscriptions"] == 0
    assert reg.stats()["grants"] == 0
    assert reg.stats()["revoked_subscriptions"] == 1
    assert reg.stats()["outbox_items"] == 1
    items = reg.flush_outbox("c1")
    assert len(items) == 1
    assert items[0].kind == "notice"
    assert items[0].topic == "news"
    assert reg.broadcast("news", "after-revoke", now=NOW) == 0


def test_revoke_without_subscriptions_changes_nothing():
    reg = authorizer()
    reg.connect("c1")
    assert reg.revoke_grant("never-subscribed", now=NOW) == {
        "revoked_connections": 0, "removed_subscriptions": 0, "notices": 0}
    assert reg.stats() == {"connections": 1, "subscriptions": 0, "grants": 0,
                           "outbox_items": 0, "broadcast_delivered": 0,
                           "broadcast_dropped": 0, "revoked_subscriptions": 0}


def test_snapshot_restore_roundtrip_is_detached():
    import copy

    reg = authorizer()
    subscriber(reg, "c1", "news")
    state = reg.snapshot()
    assert state["version"] == 1
    clone = authorizer()
    clone.restore(copy.deepcopy(state))
    assert clone.stats() == reg.stats()
    assert clone.broadcast("news", "clone-msg", now=NOW) == 1
    assert reg.stats()["broadcast_delivered"] == 0
    state.clear()
    assert reg.broadcast("news", "live-msg", now=NOW) == 1
    assert reg.stats()["connections"] == 1
    assert reg.stats()["subscriptions"] == 1


def test_restore_rejects_malformed_snapshot_and_keeps_state():
    reg = authorizer()
    subscriber(reg, "c1", "news")
    bad_states = [
        None,
        {},
        {"version": 99},
        {"version": 1, "connections": "not-a-sequence"},
    ]
    for bad in bad_states:
        with pytest.raises(wa.WsAuthError) as exc:
            reg.restore(bad)
        assert exc.value.code == "WS_SNAPSHOT_INVALID"
    assert reg.broadcast("news", "still-live", now=NOW) == 1
    assert reg.stats()["subscriptions"] == 1
