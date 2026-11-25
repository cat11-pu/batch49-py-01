import copy

import pytest

import pooled_conn_governor as pg

NOW = "2026-11-25T12:00:00+00:00"
T01 = "2026-11-25T12:00:01+00:00"
T04 = "2026-11-25T12:00:04+00:00"
T06 = "2026-11-25T12:00:06+00:00"
T11 = "2026-11-25T12:00:11+00:00"
T31 = "2026-11-25T12:00:31+00:00"


def governor(**limits):
    pool = pg.PooledConnGovernor()
    pool.configure("db", **limits)
    return pool


def test_configure_creates_target_with_zeroed_stats():
    pool = pg.PooledConnGovernor()
    config = pool.configure("db", max_size=3, max_idle=1)
    assert config["target"] == "db"
    assert config["max_size"] == 3
    assert config["max_idle"] == 1
    assert pool.stats() == {"targets": 1, "connections": 0, "idle": 0,
                            "leased": 0, "waiters": 0, "acquired": 0,
                            "reused": 0, "closed": 0, "reclaimed": 0,
                            "leaks": 0}


def test_configure_rejects_invalid_limits():
    pool = pg.PooledConnGovernor()
    bad_limits = [
        {"max_size": 0},
        {"max_size": 2, "max_idle": 3},
        {"max_requests": 0},
        {"idle_ttl_s": 0},
        {"lease_ttl_s": -1},
        {"max_waiters": -1},
    ]
    for limits in bad_limits:
        with pytest.raises(pg.PoolError) as exc:
            pool.configure("db", **limits)
        assert exc.value.code == "POOL_INVALID_CONFIG"
    assert pool.stats()["targets"] == 0


def test_unknown_target_and_connection_are_reported():
    pool = pg.PooledConnGovernor()
    with pytest.raises(pg.PoolError) as exc:
        pool.acquire("ghost", NOW)
    assert exc.value.code == "POOL_UNKNOWN_TARGET"
    pool.configure("db")
    with pytest.raises(pg.PoolError) as exc:
        pool.release("db#99", now=NOW)
    assert exc.value.code == "POOL_UNKNOWN_CONN"
    assert pool.discard("db#99") is False


def test_acquire_opens_new_connection():
    pool = governor()
    result = pool.acquire("db", NOW, owner="api")
    assert result.status == "acquired"
    assert result.reused is False
    assert result.waiter_id is None
    conn = result.conn
    assert conn.target == "db"
    assert conn.owner == "api"
    assert conn.requests == 1
    assert conn.created_at == NOW
    assert conn.leased_since == NOW
    assert pool.stats() == {"targets": 1, "connections": 1, "idle": 0,
                            "leased": 1, "waiters": 0, "acquired": 1,
                            "reused": 0, "closed": 0, "reclaimed": 0,
                            "leaks": 0}


def test_release_then_acquire_reuses_idle_connection():
    pool = governor()
    first = pool.acquire("db", NOW, owner="api").conn
    outcome = pool.release(first.conn_id, healthy=True, now=T01)
    assert outcome["conn_id"] == first.conn_id
    assert outcome["outcome"] == "idle"
    assert outcome["promoted"] is None
    assert pool.stats()["idle"] == 1
    assert pool.stats()["leased"] == 0
    second = pool.acquire("db", T01, owner="api")
    assert second.reused is True
    assert second.conn.conn_id == first.conn_id
    assert second.conn.requests == 2
    assert pool.stats()["reused"] == 1


def test_unhealthy_release_closes_connection():
    pool = governor()
    first = pool.acquire("db", NOW).conn
    outcome = pool.release(first.conn_id, healthy=False, now=T01)
    assert outcome["outcome"] == "closed"
    assert outcome["reason"] == "unhealthy"
    assert pool.stats()["closed"] == 1
    assert pool.stats()["connections"] == 0
    second = pool.acquire("db", T01)
    assert second.reused is False
    assert second.conn.conn_id != first.conn_id


def test_release_closes_connection_at_request_limit():
    pool = governor(max_requests=1)
    first = pool.acquire("db", NOW).conn
    outcome = pool.release(first.conn_id, healthy=True, now=T01)
    assert outcome["outcome"] == "closed"
    assert outcome["reason"] == "max_requests"
    assert pool.stats()["closed"] == 1
    assert pool.acquire("db", T01).reused is False


def test_release_closes_connection_past_lifetime():
    pool = governor(max_lifetime_s=10)
    first = pool.acquire("db", NOW).conn
    outcome = pool.release(first.conn_id, healthy=True, now=T11)
    assert outcome["outcome"] == "closed"
    assert outcome["reason"] == "max_lifetime"
    assert pool.stats()["closed"] == 1


def test_discard_removes_connection_without_reuse():
    pool = governor()
    first = pool.acquire("db", NOW).conn
    assert pool.discard(first.conn_id, "corrupt") is True
    assert pool.discard(first.conn_id, "corrupt") is False
    assert pool.stats()["closed"] == 1
    assert pool.stats()["connections"] == 0
    second = pool.acquire("db", T01)
    assert second.reused is False
    assert second.conn.conn_id != first.conn_id


def test_reap_closes_only_idle_connections_past_ttl():
    pool = governor(max_size=3, idle_ttl_s=30, lease_ttl_s=120)
    leased_conn = pool.acquire("db", NOW).conn
    idle_conn = pool.acquire("db", NOW).conn
    pool.release(idle_conn.conn_id, healthy=True, now=NOW)
    result = pool.reap(T31)
    assert result == {"idle_closed": 1, "leases_reclaimed": 0, "leaks": 0}
    assert pool.stats()["idle"] == 0
    assert pool.stats()["leased"] == 1
    assert pool.stats()["connections"] == 1
    assert pool.leak_report() == []
    assert leased_conn.conn_id != idle_conn.conn_id


def test_full_pool_queues_waiters_with_positions():
    pool = governor(max_size=1)
    pool.acquire("db", NOW, owner="first")
    second = pool.acquire("db", NOW, owner="second")
    assert second.status == "queued"
    assert second.conn is None
    assert second.waiter_id == "w1"
    assert second.position == 1
    third = pool.acquire("db", NOW, owner="third")
    assert third.waiter_id == "w2"
    assert third.position == 2
    assert pool.stats()["waiters"] == 2
    assert pool.stats()["connections"] == 1


def test_poll_waiter_waits_then_times_out():
    pool = governor(max_size=1, wait_timeout_s=5)
    pool.acquire("db", NOW)
    queued = pool.acquire("db", NOW, owner="waiter")
    waiting = pool.poll_waiter(queued.waiter_id, T04)
    assert waiting == {"status": "waiting", "conn": None}
    expired = pool.poll_waiter(queued.waiter_id, T06)
    assert expired == {"status": "timeout", "conn": None}
    assert pool.stats()["waiters"] == 0
    with pytest.raises(pg.PoolError) as exc:
        pool.poll_waiter(queued.waiter_id, T06)
    assert exc.value.code == "POOL_WAIT_TIMEOUT"


def test_healthy_release_hands_connection_to_first_waiter():
    pool = governor(max_size=1)
    held = pool.acquire("db", NOW, owner="api").conn
    queued = pool.acquire("db", NOW, owner="worker")
    outcome = pool.release(held.conn_id, healthy=True, now=T01)
    assert outcome["outcome"] == "handed_off"
    assert outcome["promoted"] == queued.waiter_id
    delivered = pool.poll_waiter(queued.waiter_id, T01)
    assert delivered["status"] == "acquired"
    assert delivered["conn"].conn_id == held.conn_id
    assert delivered["conn"].owner == "worker"
    assert pool.stats()["leased"] == 1
    assert pool.stats()["waiters"] == 0


def test_snapshot_restore_preserves_pool_state():
    pool = governor(max_size=2)
    conn = pool.acquire("db", NOW, owner="api").conn
    pool.release(conn.conn_id, healthy=True, now=NOW)
    state = pool.snapshot()
    assert state["version"] == 1
    clone = pg.PooledConnGovernor()
    clone.restore(copy.deepcopy(state))
    assert clone.stats() == pool.stats()
    reused = clone.acquire("db", T01)
    assert reused.reused is True
    assert reused.conn.conn_id == conn.conn_id
    state.clear()
    assert pool.stats()["connections"] == 1
    assert pool.stats()["idle"] == 1


def test_restore_rejects_malformed_state_and_keeps_pool_usable():
    pool = governor()
    first = pool.acquire("db", NOW).conn
    for bad in (None, {}, {"version": 3}, {"version": 1, "targets": "db"}):
        with pytest.raises(pg.PoolError) as exc:
            pool.restore(bad)
        assert exc.value.code == "POOL_SNAPSHOT_INVALID"
    assert pool.release(first.conn_id, healthy=True, now=T01)["outcome"] == "idle"
    assert pool.acquire("db", T01).conn.conn_id == first.conn_id
