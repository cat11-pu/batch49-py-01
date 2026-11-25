import copy
import threading

import pytest

import lock_order_deadlock_guard as lg


def guard(**kwargs):
    return lg.LockOrderDeadlockGuard(**kwargs)


def armed(**kwargs):
    table = guard(**kwargs)
    for name in ("r1", "r2", "r3"):
        table.register_resource(name)
    return table


def test_register_rejects_duplicate_and_reports_version():
    table = guard()
    assert table.order_version() == 0
    assert table.register_resource("r1") == 1
    assert table.register_resource("r2", rank=5) == 2
    assert table.order_version() == 2
    with pytest.raises(lg.LockOrderError) as exc:
        table.register_resource("r1")
    assert exc.value.code == "RESOURCE_EXISTS"
    assert table.order_version() == 2
    assert table.stats()["acquired"] == 0


def test_acquire_many_uses_global_rank_regardless_of_input_order():
    table = armed()
    got = table.acquire_many(["r3", "r1", "r2"], owner="alpha")
    assert got == ("r1", "r2", "r3")
    assert tuple(table.held(owner="alpha")) == ("r1", "r2", "r3")
    assert table.wait_graph() == {}
    assert table.stats()["requests"] == 1
    assert table.stats()["acquired"] == 3


def test_unknown_resource_is_rejected_without_state_change():
    table = armed()
    with pytest.raises(lg.LockOrderError) as exc:
        table.acquire_many(["r1", "ghost"], owner="alpha")
    assert exc.value.code == "UNKNOWN_RESOURCE"
    assert exc.value.detail == "ghost"
    assert table.held(owner="alpha") == {}
    assert table.stats()["acquired"] == 0


def test_reverse_order_request_is_rejected_and_reports_pair():
    table = armed()
    table.acquire_many(["r3"], owner="alpha")
    with pytest.raises(lg.LockOrderError) as exc:
        table.acquire_many(["r1", "r2"], owner="alpha")
    assert exc.value.code == "ORDER_VIOLATION"
    assert exc.value.detail == {"held": "r3", "requested": "r1"}
    assert table.held(owner="alpha") == {"r3": 1}
    assert table.held(owner="beta") == {}
    assert table.stats()["violations"] == 1


def test_failed_acquire_releases_earlier_locks_from_same_request():
    table = armed()
    table.acquire_many(["r3"], owner="alpha", mode="exclusive")
    with pytest.raises(lg.LockOrderError) as exc:
        table.acquire_many(["r1", "r2", "r3"], owner="beta", timeout=0)
    assert exc.value.code == "LOCK_TIMEOUT"
    assert exc.value.detail["resource"] == "r3"
    assert table.held(owner="beta") == {}
    assert table.held(owner="alpha") == {"r3": 1}
    assert table.acquire_many(["r1", "r2"], owner="gamma") == ("r1", "r2")
    assert table.stats()["timeouts"] == 1
    assert table.stats()["acquired"] == 3


def test_reentrant_acquire_and_release_decrement():
    table = armed()
    table.acquire_many(["r1", "r2"], owner="alpha")
    table.acquire_many(["r2"], owner="alpha")
    assert table.held(owner="alpha") == {"r1": 1, "r2": 2}
    assert table.release("r2", owner="alpha") == 1
    assert table.held(owner="alpha") == {"r1": 1, "r2": 1}
    assert table.release("r2", owner="alpha") == 0
    assert table.held(owner="alpha") == {"r1": 1}
    assert table.stats()["reentrant"] == 1
    assert table.stats()["released"] == 1
    with pytest.raises(lg.LockOrderError) as exc:
        table.release("r2", owner="alpha")
    assert exc.value.code == "NOT_HELD"


def test_release_all_is_idempotent_and_returns_distinct_count():
    table = armed()
    table.acquire_many(["r1", "r2"], owner="alpha")
    table.acquire_many(["r2"], owner="alpha")
    assert table.release_all(owner="alpha") == 2
    assert table.held(owner="alpha") == {}
    assert table.release_all(owner="alpha") == 0
    assert table.release_all(owner="alpha") == 0
    assert table.stats()["released"] == 2
    assert table.acquire_many(["r1"], owner="beta") == ("r1",)


def test_upgrade_denied_while_other_owner_holds_shared():
    table = armed()
    table.acquire_many(["r1"], owner="alpha", mode="shared")
    table.acquire_many(["r1"], owner="beta", mode="shared")
    assert table.try_upgrade("r1", owner="beta") is False
    holders = table.snapshot()["holders"]["r1"]
    assert holders["alpha"]["mode"] == "shared"
    assert holders["beta"]["mode"] == "shared"
    assert table.stats()["upgrades"] == 0


def test_upgrade_succeeds_only_without_other_holders():
    table = armed()
    table.acquire_many(["r1"], owner="alpha", mode="shared")
    assert table.try_upgrade("r1", owner="alpha") is True
    assert table.snapshot()["holders"]["r1"]["alpha"]["mode"] == "exclusive"
    assert table.stats()["upgrades"] == 1
    with pytest.raises(lg.LockOrderError) as exc:
        table.acquire_many(["r1"], owner="beta", mode="shared", timeout=0)
    assert exc.value.code == "LOCK_TIMEOUT"
    assert table.held(owner="beta") == {}


def test_upgrade_of_unheld_resource_raises_not_held():
    table = armed()
    with pytest.raises(lg.LockOrderError) as exc:
        table.try_upgrade("r1", owner="alpha")
    assert exc.value.code == "NOT_HELD"
    table.acquire_many(["r1"], owner="beta")
    with pytest.raises(lg.LockOrderError) as exc2:
        table.try_upgrade("r1", owner="alpha")
    assert exc2.value.code == "NOT_HELD"


def test_per_request_and_global_lock_caps():
    small = guard(max_locks_per_request=2, max_locks=3)
    for name in ("a", "b", "c"):
        small.register_resource(name)
    with pytest.raises(lg.LockOrderError) as exc:
        small.acquire_many(["a", "b", "c"], owner="alpha")
    assert exc.value.code == "TOO_MANY_LOCKS"
    assert small.held(owner="alpha") == {}
    assert small.stats()["requests"] == 1

    tiny = guard(max_locks=2)
    tiny.register_resource("a")
    tiny.register_resource("b")
    with pytest.raises(lg.LockOrderError) as exc2:
        tiny.register_resource("c")
    assert exc2.value.code == "LOCK_TABLE_FULL"
    assert tiny.order_version() == 2


def test_snapshot_is_detached_and_restore_roundtrips():
    table = armed()
    table.acquire_many(["r1", "r2"], owner="alpha", mode="shared")
    table.register_resource("r4")
    state = table.snapshot()
    assert set(state) == {"order_version", "order", "holders", "stats"}
    pristine = copy.deepcopy(state)
    state["holders"]["r1"]["alpha"]["count"] = 99
    state["stats"]["acquired"] = 999
    state["order"].clear()
    assert table.held(owner="alpha") == {"r1": 1, "r2": 1}
    assert table.stats()["acquired"] == 2
    assert table.order_version() == 4

    clone = guard()
    clone.restore(pristine)
    assert clone.held(owner="alpha") == {"r1": 1, "r2": 1}
    assert clone.order_version() == 4
    assert clone.stats() == table.stats()
    assert clone.snapshot()["holders"]["r1"]["alpha"]["mode"] == "shared"


def test_restore_rejects_malformed_state():
    table = armed()
    table.acquire_many(["r1"], owner="alpha")
    bad_states = [
        None,
        {},
        {"order_version": "3", "order": {}, "holders": {}, "stats": {}},
        {"order_version": 3, "order": [], "holders": {}, "stats": {}},
        {"order_version": 3, "order": {}, "holders": [], "stats": {}},
        {"order_version": 3, "order": {}, "holders": {}, "stats": {"acquired": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(lg.LockOrderError) as exc:
            table.restore(bad)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert table.held(owner="alpha") == {"r1": 1}
    assert table.order_version() == 3
    assert table.stats()["acquired"] == 1


def test_stats_track_requests_acquired_released_upgrades():
    table = armed()
    assert table.stats() == {"requests": 0, "acquired": 0, "reentrant": 0,
                             "released": 0, "upgrades": 0, "timeouts": 0,
                             "violations": 0}
    table.acquire_many(["r1", "r2"], owner="alpha")
    table.release_all(owner="alpha")
    table.acquire_many(["r1"], owner="beta", mode="exclusive")
    table.acquire_many(["r1"], owner="beta", mode="exclusive")
    table.try_upgrade("r1", owner="beta")
    with pytest.raises(lg.LockOrderError):
        table.acquire_many(["r1"], owner="gamma", mode="shared", timeout=0)
    table.acquire_many(["r2"], owner="delta")
    with pytest.raises(lg.LockOrderError):
        table.acquire_many(["r1"], owner="delta")
    assert table.stats() == {"requests": 6, "acquired": 4, "reentrant": 1,
                             "released": 2, "upgrades": 1, "timeouts": 1,
                             "violations": 1}


def test_concurrent_acquisition_of_distinct_resources_succeeds():
    table = guard(max_locks=8)
    for index in range(8):
        table.register_resource(f"r{index}")
    errors = []
    barrier = threading.Barrier(8)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            table.acquire_many([f"r{index}"], owner=f"o{index}", mode="exclusive")
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    for index in range(8):
        assert table.held(owner=f"o{index}") == {f"r{index}": 1}
    assert table.stats()["acquired"] == 8
