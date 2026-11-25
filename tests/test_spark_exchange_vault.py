import copy
import threading

import pytest

import spark_exchange_vault as sv

T0 = 10_000
CATALOG = {"character": {"sword": 3, "shield": 5}, "weapon": {"bow": 2}}


def vault(**kwargs):
    kwargs.setdefault("catalog", CATALOG)
    return sv.SparkExchangeVault(**kwargs)


def test_register_pool_and_pull_awards_sparks():
    store = vault(spark_ttl=1000)
    store.register_pool("pool-a", "character")
    results = store.pull("pool-a", count=3, now=T0)
    assert [row.index for row in results] == [0, 1, 2]
    assert [row.rarity for row in results] == ["normal", "normal", "normal"]
    assert [row.pity_after for row in results] == [1, 2, 3]
    assert [row.sparks_awarded for row in results] == [1, 1, 1]
    assert store.sparks_of("character") == 3
    assert store.stats()["pulls"] == 3
    assert store.stats()["sparks_granted"] == 3


def test_pull_rejects_unknown_pool_and_bad_count():
    store = vault()
    with pytest.raises(sv.SparkError) as exc:
        store.pull("nope", now=T0)
    assert exc.value.code == "SPARK_UNKNOWN_POOL"

    store.register_pool("pool-a", "character")
    for bad in (0, -3, 51, 2.5):
        with pytest.raises(sv.SparkError) as exc:
            store.pull("pool-a", count=bad, now=T0)
        assert exc.value.code == "SPARK_INVALID_REQUEST"

    with pytest.raises(sv.SparkError) as exc:
        store.register_pool("pool-a", "weapon")
    assert exc.value.code == "SPARK_POOL_CONFLICT"
    assert store.sparks_of("character") == 0


def test_pity_threshold_forces_featured_and_resets():
    store = vault(pity_threshold=3)
    store.register_pool("pool-a", "character")
    rarities = [store.pull("pool-a", now=T0)[0].rarity for _ in range(4)]
    assert rarities == ["normal", "normal", "featured", "normal"]
    assert store.pity_count("character") == 1
    assert store.sparks_of("character") == 4


def test_sparks_and_pity_are_tracked_per_pool_type():
    store = vault()
    store.register_pool("pool-a", "character")
    store.register_pool("pool-b", "weapon")
    store.pull("pool-a", count=2, now=T0)
    store.pull("pool-b", count=3, now=T0)
    assert store.sparks_of("character") == 2
    assert store.sparks_of("weapon") == 3
    assert store.pity_count("character") == 2
    assert store.pity_count("weapon") == 3


def test_exchange_deducts_and_returns_receipt():
    store = vault(spark_ttl=1000)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=5, now=T0)
    receipt = store.exchange("req-1", "pool-a", "sword", now=T0 + 10)
    assert (receipt.request_id, receipt.pool_type, receipt.item_id) == (
        "req-1", "character", "sword")
    assert receipt.cost == 3
    assert receipt.sparks_after == 2
    assert receipt.status == "ok"
    assert store.sparks_of("character") == 2
    assert store.stats()["sparks_spent"] == 3
    assert store.stats()["exchanges"] == 1


def test_exchange_errors_leave_state_intact():
    store = vault()
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=2, now=T0)
    cases = [
        ("req-a", "pool-a", "shield", "SPARK_INSUFFICIENT_SPARKS"),
        ("req-b", "pool-a", "axe", "SPARK_UNKNOWN_ITEM"),
        ("req-c", "missing", "sword", "SPARK_UNKNOWN_POOL"),
        ("", "pool-a", "sword", "SPARK_INVALID_REQUEST"),
    ]
    for request_id, pool_id, item_id, code in cases:
        with pytest.raises(sv.SparkError) as exc:
            store.exchange(request_id, pool_id, item_id, now=T0)
        assert exc.value.code == code
    assert store.sparks_of("character") == 2
    assert store.stats()["exchanges"] == 0


def test_quote_reports_without_mutation():
    store = vault()
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=4, now=T0)
    low = store.quote("req-q1", "pool-a", "shield", now=T0)
    assert (low.cost, low.sparks, low.affordable) == (5, 4, False)
    assert low.request_id == "req-q1"
    ok = store.quote("req-q2", "pool-a", "sword", now=T0)
    assert (ok.cost, ok.sparks, ok.affordable) == (3, 4, True)
    assert store.sparks_of("character") == 4
    assert store.stats()["sparks_spent"] == 0
    assert store.stats()["exchanges"] == 0


def test_carry_over_moves_within_type():
    store = vault(spark_ttl=1000)
    store.register_pool("pool-a", "character")
    store.register_pool("pool-b", "character")
    store.pull("pool-a", count=5, now=T0)
    assert store.carry_over("pool-a", "pool-b", now=T0 + 10) == 5
    assert store.sparks_of("character") == 5
    receipt = store.exchange("req-2", "pool-b", "sword", now=T0 + 20)
    assert receipt.sparks_after == 2


def test_carry_over_rejects_type_mismatch():
    store = vault()
    store.register_pool("pool-a", "character")
    store.register_pool("pool-c", "weapon")
    store.pull("pool-a", count=2, now=T0)
    with pytest.raises(sv.SparkError) as exc:
        store.carry_over("pool-a", "pool-c", now=T0)
    assert exc.value.code == "SPARK_POOL_TYPE_MISMATCH"
    assert store.sparks_of("character") == 2


def test_expire_drops_batches_after_ttl():
    store = vault(spark_ttl=100)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=4, now=T0)
    assert store.expire(now=T0 + 99) == 0
    assert store.sparks_of("character") == 4
    assert store.expire(now=T0 + 100) == 4
    assert store.sparks_of("character") == 0
    assert store.expire(now=T0 + 101) == 0
    assert store.stats()["sparks_expired"] == 4


def test_distinct_requests_each_apply_once():
    store = vault(spark_ttl=1000)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=8, now=T0)
    first = store.exchange("req-1", "pool-a", "sword", now=T0)
    second = store.exchange("req-2", "pool-a", "shield", now=T0)
    assert (first.sparks_after, second.sparks_after) == (5, 0)
    assert store.sparks_of("character") == 0
    assert store.stats()["exchanges"] == 2
    assert store.stats()["sparks_spent"] == 8


def test_concurrent_exchanges_never_overspend():
    store = sv.SparkExchangeVault(catalog={"character": {"token": 1}},
                                  spark_ttl=10_000)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=3, now=T0)
    barrier = threading.Barrier(8)
    accepted = []
    refused = []
    guard = threading.Lock()

    def worker(index):
        try:
            barrier.wait(timeout=5)
            receipt = store.exchange(f"req-{index}", "pool-a", "token", now=T0 + 5)
            with guard:
                accepted.append(receipt.request_id)
        except sv.SparkError as exc:
            with guard:
                refused.append(exc.code)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 3
    assert len(refused) == 5
    assert set(refused) == {"SPARK_INSUFFICIENT_SPARKS"}
    assert store.sparks_of("character") == 0
    assert store.stats()["exchanges"] == 3


def test_snapshot_is_detached():
    store = vault(spark_ttl=1000)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=3, now=T0)
    state = store.snapshot()
    assert set(state) == {"schema", "pools", "batches", "pity", "requests", "stats"}
    state["batches"].clear()
    state["stats"]["pulls"] = 99
    assert store.sparks_of("character") == 3
    assert store.stats()["pulls"] == 3


def test_restore_roundtrip_and_rejects_malformed():
    store = vault(spark_ttl=5000)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=5, now=T0)
    store.exchange("req-1", "pool-a", "sword", now=T0)
    state = store.snapshot()

    clone = sv.SparkExchangeVault(catalog=CATALOG, spark_ttl=5000)
    clone.restore(copy.deepcopy(state))
    assert clone.sparks_of("character") == 2
    assert clone.stats()["sparks_granted"] == 5
    assert clone.stats()["exchanges"] == 1

    bad_states = [
        None,
        {},
        {"schema": 2, "pools": {}, "batches": [], "pity": {},
         "requests": {}, "stats": {}},
        {"schema": 1, "pools": [], "batches": [], "pity": {},
         "requests": {}, "stats": {}},
    ]
    for bad in bad_states:
        with pytest.raises(sv.SparkError) as exc:
            clone.restore(bad)
        assert exc.value.code == "SPARK_STATE_INVALID"
    assert clone.sparks_of("character") == 2


def test_sparks_of_skips_expired_batches_when_now_given():
    store = vault(spark_ttl=100)
    store.register_pool("pool-a", "character")
    store.pull("pool-a", count=4, now=T0)
    assert store.sparks_of("character", now=T0 + 99) == 4
    assert store.sparks_of("character", now=T0 + 100) == 0
    assert store.sparks_of("character") == 4
    assert store.expire(now=T0 + 100) == 4
