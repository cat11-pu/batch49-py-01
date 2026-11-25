import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import bus_routing_graph as rg


def build(**kwargs):
    params = {
        "master_id": "master",
        "max_buses": 16,
        "max_connections": 32,
        "max_depth": 4,
    }
    params.update(kwargs)
    return rg.BusRoutingGraph(**params)


def parallel_graph():
    graph = build()
    graph.add_bus("a", gain=0.5, delay_frames=10)
    graph.add_bus("b", gain=2.0, delay_frames=5)
    graph.connect("a", "master", gain=0.8, delay_frames=2)
    graph.connect("a", "b", gain=0.5, delay_frames=2)
    graph.connect("b", "master", gain=1.0, delay_frames=2)
    return graph


def test_add_bus_validation_and_capacity():
    graph = build()
    assert graph.buses() == ["master"]
    graph.add_bus("a", gain=0.5)
    assert graph.buses() == ["a", "master"]
    with pytest.raises(rg.BusError) as info:
        graph.add_bus("a")
    assert info.value.code == "BUS_DUPLICATE"
    capped = build(max_buses=3)
    capped.add_bus("a")
    capped.add_bus("b")
    with pytest.raises(rg.BusError) as info:
        capped.add_bus("c")
    assert info.value.code == "BUS_LIMIT"
    assert capped.buses() == ["a", "b", "master"]


def test_connect_rejects_unknown_buses():
    graph = build()
    with pytest.raises(rg.BusError) as info:
        graph.connect("ghost", "master")
    assert info.value.code == "UNKNOWN_BUS"
    graph.add_bus("a")
    with pytest.raises(rg.BusError) as info:
        graph.connect("a", "nowhere")
    assert info.value.code == "UNKNOWN_BUS"


def test_connect_rejects_self_loop():
    graph = build()
    graph.add_bus("a")
    with pytest.raises(rg.BusError) as info:
        graph.connect("a", "a")
    assert info.value.code == "SELF_LOOP"
    assert graph.connections() == []


def test_connect_rejects_cycle():
    graph = build()
    for bus_id in ("a", "b", "c"):
        graph.add_bus(bus_id)
    graph.connect("a", "b")
    graph.connect("b", "c")
    with pytest.raises(rg.BusError) as info:
        graph.connect("c", "a")
    assert info.value.code == "CYCLE"
    assert len(graph.connections()) == 2


def test_connect_enforces_depth_limit():
    shallow = build(max_depth=3)
    for bus_id in ("a", "b", "c"):
        shallow.add_bus(bus_id)
    shallow.connect("c", "b")
    shallow.connect("b", "a")
    with pytest.raises(rg.BusError) as info:
        shallow.connect("a", "master")
    assert info.value.code == "DEPTH_LIMIT"
    assert len(shallow.connections()) == 2

    deep = build(max_depth=4)
    for bus_id in ("a", "b", "c"):
        deep.add_bus(bus_id)
    deep.connect("c", "b")
    deep.connect("b", "a")
    deep.connect("a", "master")
    assert len(deep.connections()) == 3


def test_connection_capacity_is_enforced():
    graph = build(max_connections=2)
    for bus_id in ("a", "b", "c"):
        graph.add_bus(bus_id)
    graph.connect("a", "master")
    graph.connect("b", "master")
    with pytest.raises(rg.BusError) as info:
        graph.connect("c", "master")
    assert info.value.code == "CONNECTION_LIMIT"
    assert len(graph.connections()) == 2


def test_effective_gain_for_single_path():
    graph = build()
    graph.add_bus("a", gain=0.5)
    graph.connect("a", "master", gain=0.8)
    assert graph.effective_gain("a") == pytest.approx(0.4)
    assert graph.effective_gain("master") == pytest.approx(1.0)


def test_effective_gain_sums_parallel_paths():
    graph = parallel_graph()
    assert graph.effective_gain("a") == pytest.approx(0.9)
    assert graph.effective_gain("b") == pytest.approx(0.5)


def test_effective_delay_follows_strongest_path():
    graph = parallel_graph()
    assert graph.effective_delay("a") == 19
    assert graph.effective_delay("b") == 7


def test_mute_zeroes_every_path_through_the_bus():
    graph = parallel_graph()
    graph.mute("b")
    assert graph.effective_gain("a") == pytest.approx(0.4)
    assert graph.effective_gain("b") == pytest.approx(0.0)
    graph.mute("master")
    assert graph.effective_gain("a") == pytest.approx(0.0)
    graph.mute("master", False)
    graph.mute("b", False)
    assert graph.effective_gain("a") == pytest.approx(0.9)
    with pytest.raises(rg.BusError) as info:
        graph.mute("ghost")
    assert info.value.code == "UNKNOWN_BUS"


def test_solo_mutes_unrelated_buses():
    graph = build()
    for bus_id in ("x", "y", "z"):
        graph.add_bus(bus_id, gain=0.5)
        graph.connect(bus_id, "master", gain=1.0)
    assert graph.effective_gain("x") == pytest.approx(0.5)
    graph.solo("y")
    assert graph.effective_gain("y") == pytest.approx(0.5)
    assert graph.effective_gain("x") == pytest.approx(0.0)
    assert graph.effective_gain("z") == pytest.approx(0.0)
    graph.solo("y", False)
    assert graph.effective_gain("x") == pytest.approx(0.5)
    with pytest.raises(rg.BusError) as info:
        graph.solo("ghost")
    assert info.value.code == "UNKNOWN_BUS"


def test_topology_order_is_deterministic():
    graph = build()
    for bus_id in ("a", "b", "zz"):
        graph.add_bus(bus_id)
    graph.connect("b", "a")
    graph.connect("a", "master")
    assert graph.topology_order() == ["b", "a", "master", "zz"]


def test_disconnect_removes_edge_and_path():
    graph = parallel_graph()
    graph.disconnect("a", "b")
    assert graph.effective_gain("a") == pytest.approx(0.4)
    assert len(graph.connections()) == 1
    with pytest.raises(rg.BusError) as info:
        graph.disconnect("a", "b")
    assert info.value.code == "NOT_CONNECTED"


def test_snapshot_restore_round_trip_and_invalid_state():
    graph = parallel_graph()
    saved = graph.snapshot()
    graph.add_bus("q")
    graph.mute("a")
    graph.solo("b")
    graph.restore(saved)
    assert graph.buses() == ["a", "b", "master"]
    assert graph.effective_gain("a") == pytest.approx(0.9)
    assert graph.effective_gain("b") == pytest.approx(0.5)
    with pytest.raises(rg.BusError) as info:
        graph.restore({"buses": []})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert graph.effective_gain("a") == pytest.approx(0.9)


def test_concurrent_builds_end_in_a_complete_graph():
    graph = build()
    barrier = threading.Barrier(8)

    def build_one(index):
        barrier.wait()
        bus_id = "bus%d" % index
        graph.add_bus(bus_id, gain=1.0)
        graph.connect(bus_id, "master", gain=1.0)
        return bus_id

    with ThreadPoolExecutor(max_workers=8) as pool:
        names = list(pool.map(build_one, range(8)))
    assert graph.buses() == sorted(names + ["master"])
    for name in names:
        assert graph.effective_gain(name) == pytest.approx(1.0)
    order = graph.topology_order()
    assert len(order) == 9
    assert set(order) == set(names + ["master"])
