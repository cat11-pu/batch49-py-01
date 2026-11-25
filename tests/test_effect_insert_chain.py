import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import effect_insert_chain as ec


def build(**kwargs):
    params = {
        "slots_per_bus": 4,
        "max_buses": 8,
        "block_size": 64,
        "max_tail_frames": 200,
    }
    params.update(kwargs)
    return ec.EffectInsertChain(**params)


def chain_with(effects):
    chain = build()
    chain.add_bus("bus")
    for effect_id, latency, tail in effects:
        chain.register_effect(effect_id, latency_frames=latency, tail_frames=tail)
    return chain


def test_add_bus_and_register_effect_validation():
    chain = build()
    chain.add_bus("bus")
    with pytest.raises(ec.EffectError) as info:
        chain.add_bus("bus")
    assert info.value.code == "BUS_DUPLICATE"
    chain.register_effect("eq", latency_frames=5, tail_frames=0)
    with pytest.raises(ec.EffectError) as info:
        chain.register_effect("eq", latency_frames=1)
    assert info.value.code == "EFFECT_DUPLICATE"
    with pytest.raises(ec.EffectError) as info:
        chain.register_effect("bad", latency_frames=-1)
    assert info.value.code == "INVALID_EFFECT"
    with pytest.raises(ec.EffectError) as info:
        chain.register_effect("long", latency_frames=1, tail_frames=201)
    assert info.value.code == "TAIL_LIMIT"
    capped = build(max_buses=1)
    capped.add_bus("one")
    with pytest.raises(ec.EffectError) as info:
        capped.add_bus("two")
    assert info.value.code == "BUS_LIMIT"


def test_insert_appends_and_returns_slot_view():
    chain = chain_with([("a", 3, 0), ("b", 5, 0)])
    view = chain.insert("bus", "a")
    assert (view.bus_id, view.position, view.effect_id) == ("bus", 0, "a")
    assert view.bypassed is False
    assert view.latency_frames == 3
    assert view.state == "active"
    chain.insert("bus", "b")
    assert chain.order("bus") == ["a", "b"]


def test_insert_at_position_shifts_existing_slots():
    chain = chain_with([("a", 3, 0), ("b", 5, 0), ("c", 7, 0)])
    chain.insert("bus", "a")
    chain.insert("bus", "b")
    view = chain.insert("bus", "c", position=1)
    assert view.position == 1
    assert chain.order("bus") == ["a", "c", "b"]


def test_insert_rejects_unknown_ids_and_bad_position():
    chain = chain_with([("a", 3, 0)])
    with pytest.raises(ec.EffectError) as info:
        chain.insert("ghost", "a")
    assert info.value.code == "UNKNOWN_BUS"
    with pytest.raises(ec.EffectError) as info:
        chain.insert("bus", "ghost")
    assert info.value.code == "UNKNOWN_EFFECT"
    with pytest.raises(ec.EffectError) as info:
        chain.insert("bus", "a", position=1)
    assert info.value.code == "POSITION_RANGE"


def test_duplicate_effect_in_one_chain_is_rejected():
    chain = chain_with([("a", 3, 0)])
    chain.insert("bus", "a")
    with pytest.raises(ec.EffectError) as info:
        chain.insert("bus", "a")
    assert info.value.code == "EFFECT_IN_CHAIN"
    assert chain.order("bus") == ["a"]


def test_slot_capacity_is_enforced():
    chain = build(slots_per_bus=2)
    chain.add_bus("bus")
    for name in ("a", "b", "c"):
        chain.register_effect(name, latency_frames=1)
    chain.insert("bus", "a")
    chain.insert("bus", "b")
    with pytest.raises(ec.EffectError) as info:
        chain.insert("bus", "c")
    assert info.value.code == "SLOT_LIMIT"
    assert chain.order("bus") == ["a", "b"]


def test_bypass_keeps_order_and_toggles_state():
    chain = chain_with([("a", 3, 0), ("b", 5, 0), ("c", 7, 0)])
    for name in ("a", "b", "c"):
        chain.insert("bus", name)
    view = chain.bypass("bus", "b")
    assert view.bypassed is True
    assert view.state == "bypassed"
    assert chain.order("bus") == ["a", "b", "c"]
    back = chain.bypass("bus", "b", False)
    assert back.bypassed is False
    assert chain.order("bus") == ["a", "b", "c"]
    with pytest.raises(ec.EffectError) as info:
        chain.bypass("bus", "ghost")
    assert info.value.code == "EFFECT_NOT_IN_CHAIN"


def test_move_reorders_and_validates_target():
    chain = chain_with([("a", 1, 0), ("b", 1, 0), ("c", 1, 0)])
    for name in ("a", "b", "c"):
        chain.insert("bus", name)
    assert chain.move("bus", "a", 2) == ["b", "c", "a"]
    assert chain.move("bus", "b", 1) == ["c", "b", "a"]
    with pytest.raises(ec.EffectError) as info:
        chain.move("bus", "a", 3)
    assert info.value.code == "POSITION_RANGE"
    with pytest.raises(ec.EffectError) as info:
        chain.move("bus", "ghost", 0)
    assert info.value.code == "EFFECT_NOT_IN_CHAIN"


def test_remove_without_tail_frees_the_slot():
    chain = chain_with([("a", 3, 0), ("b", 5, 0)])
    chain.insert("bus", "a")
    chain.insert("bus", "b")
    result = chain.remove("bus", "b")
    assert result.state == "removed"
    assert result.tail_remaining == 0
    assert result.position == 1
    assert chain.order("bus") == ["a"]
    with pytest.raises(ec.EffectError) as info:
        chain.remove("bus", "b")
    assert info.value.code == "EFFECT_NOT_IN_CHAIN"


def test_remove_with_tail_flushes_before_release():
    chain = chain_with([("reverb", 4, 100)])
    chain.insert("bus", "reverb")
    result = chain.remove("bus", "reverb")
    assert result.state == "flushing"
    assert result.tail_remaining == 100
    assert result.position == 0
    assert chain.order("bus") == ["reverb"]
    assert chain.flush_tail("bus", effect_id="reverb", frames=60) == 40
    assert chain.flush_tail("bus", effect_id="reverb", frames=40) == 0
    assert chain.order("bus") == []
    assert chain.insert("bus", "reverb").position == 0


def test_process_block_advances_tails_and_caps_block_size():
    chain = chain_with([("t1", 0, 50), ("t2", 0, 20)])
    chain.insert("bus", "t1")
    chain.insert("bus", "t2")
    chain.remove("bus", "t1")
    chain.remove("bus", "t2")
    result = chain.process_block("bus", 30)
    assert result.frames == 30
    assert result.active == 0
    assert result.bypassed == 0
    assert result.flushing == 1
    assert result.latency_frames == 0
    assert chain.order("bus") == ["t1"]
    with pytest.raises(ec.EffectError) as info:
        chain.process_block("bus", 65)
    assert info.value.code == "BLOCK_TOO_LARGE"
    with pytest.raises(ec.EffectError) as info:
        chain.process_block("bus", 0)
    assert info.value.code == "INVALID_FRAMES"


def test_latency_sums_whole_chain_and_updates_after_removal():
    chain = chain_with([("a", 5, 0), ("b", 7, 0), ("c", 11, 0)])
    for name in ("a", "b", "c"):
        chain.insert("bus", name)
    assert chain.latency("bus") == 23
    chain.remove("bus", "b")
    assert chain.latency("bus") == 18
    chain.remove("bus", "c")
    assert chain.latency("bus") == 5


def test_chains_are_isolated_per_bus():
    chain = build()
    chain.add_bus("one")
    chain.add_bus("two")
    chain.register_effect("a", latency_frames=3)
    chain.insert("one", "a")
    assert chain.order("two") == []
    assert chain.latency("two") == 0
    with pytest.raises(ec.EffectError) as info:
        chain.remove("two", "a")
    assert info.value.code == "EFFECT_NOT_IN_CHAIN"
    with pytest.raises(ec.EffectError) as info:
        chain.order("ghost")
    assert info.value.code == "UNKNOWN_BUS"


def test_snapshot_restore_round_trip_and_invalid_state():
    chain = chain_with([("a", 4, 0), ("b", 6, 80), ("c", 2, 0)])
    chain.insert("bus", "a")
    chain.insert("bus", "b")
    chain.remove("bus", "b")
    saved = chain.snapshot()
    chain.flush_tail("bus", effect_id="b", frames=80)
    chain.insert("bus", "c")
    chain.bypass("bus", "a")
    assert chain.order("bus") == ["a", "c"]
    assert chain.latency("bus") == 6
    chain.restore(saved)
    assert chain.order("bus") == ["a", "b"]
    assert chain.latency("bus") == 10
    assert chain.flush_tail("bus", effect_id="b", frames=0) == 80
    with pytest.raises(ec.EffectError) as info:
        chain.restore({"buses": {}})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert chain.order("bus") == ["a", "b"]


def test_concurrent_inserts_are_serialized_per_bus():
    chain = build(slots_per_bus=8)
    chain.add_bus("bus")
    names = ["fx%d" % index for index in range(8)]
    for name in names:
        chain.register_effect(name, latency_frames=1)
    barrier = threading.Barrier(8)

    def add(name):
        barrier.wait()
        chain.insert("bus", name)
        return name

    with ThreadPoolExecutor(max_workers=8) as pool:
        inserted = list(pool.map(add, names))
    assert sorted(inserted) == names
    assert sorted(chain.order("bus")) == names
    assert chain.latency("bus") == 8
