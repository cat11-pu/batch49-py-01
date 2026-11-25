import copy
import threading

import pytest

import battle_log_retainer as bl


def test_append_assigns_sequential_sequence():
    log = bl.BattleLogRetainer(capacity=10)
    first = log.append("damage", {"amount": 5})
    second = log.append("heal", {"amount": 1})
    assert (first.seq, first.kind, first.pinned) == (1, "damage", False)
    assert (second.seq, second.kind) == (2, "heal")
    assert [row.seq for row in log.entries()] == [1, 2]
    assert log.size() == 2
    assert log.stats()["appended"] == 2


def test_append_rejects_blank_kind_and_bad_level():
    log = bl.BattleLogRetainer()
    for kind in ("", "   ", 5):
        with pytest.raises(bl.BattleLogError) as exc:
            log.append(kind)
        assert exc.value.code == "BATTLE_LOG_INVALID_REQUEST"
    with pytest.raises(bl.BattleLogError) as exc:
        log.append("damage", level=-1)
    assert exc.value.code == "BATTLE_LOG_INVALID_REQUEST"
    assert log.size() == 0


def test_capacity_evicts_oldest_unpinned():
    log = bl.BattleLogRetainer(capacity=3)
    for index in range(5):
        log.append("damage", {"n": index})
    assert [row.seq for row in log.entries()] == [3, 4, 5]
    assert log.stats()["evicted"] == 2


def test_pin_protects_entry_from_eviction():
    log = bl.BattleLogRetainer(capacity=2)
    log.append("death", {"who": "boss"}, level=2)
    log.pin(1)
    log.append("damage", {"n": 1})
    log.append("damage", {"n": 2})
    assert [row.seq for row in log.entries()] == [1, 3]
    assert log.entries()[0].pinned is True
    assert log.stats()["evicted"] == 1


def test_pin_and_unpin_validate_sequence():
    log = bl.BattleLogRetainer()
    log.append("damage")
    with pytest.raises(bl.BattleLogError) as exc:
        log.pin(99)
    assert exc.value.code == "BATTLE_LOG_UNKNOWN_SEQ"
    assert log.unpin(1) is False
    assert log.pin(1).pinned is True
    assert log.unpin(1) is True
    assert log.stats()["unpinned"] == 1


def test_unpinned_entry_becomes_evictable():
    log = bl.BattleLogRetainer(capacity=2)
    log.append("death")
    log.append("damage")
    log.pin(1)
    log.append("damage")
    assert [row.seq for row in log.entries()] == [1, 3]
    log.unpin(1)
    log.append("damage")
    assert [row.seq for row in log.entries()] == [3, 4]


def test_sample_rate_keeps_every_nth_ordinary_event():
    log = bl.BattleLogRetainer(capacity=10, sample_rate=0.5)
    results = [log.append("damage", {"n": index}) for index in range(6)]
    assert [row.payload["n"] for row in results if row is not None] == [1, 3, 5]
    assert [row.seq for row in log.entries()] == [1, 2, 3]
    assert log.stats()["appended"] == 3
    assert log.stats()["sampled_out"] == 3


def test_set_sample_rate_validates_range():
    log = bl.BattleLogRetainer()
    for bad in (0, -0.5, 1.5, "fast"):
        with pytest.raises(bl.BattleLogError) as exc:
            log.set_sample_rate(bad)
        assert exc.value.code == "BATTLE_LOG_INVALID_REQUEST"
    log.set_sample_rate(0.25)
    log.append("damage", {"n": 0})
    assert log.size() == 0
    assert log.stats()["sampled_out"] == 1


def test_export_range_is_inclusive_and_ordered():
    log = bl.BattleLogRetainer(capacity=10)
    for index in range(5):
        log.append("damage", {"n": index})
    window = log.export_range(2, 4)
    assert [row["seq"] for row in window] == [2, 3, 4]
    assert [row["payload"]["n"] for row in window] == [1, 2, 3]
    assert [row["kind"] for row in window] == ["damage", "damage", "damage"]
    assert log.stats()["exported"] == 3


def test_export_range_rejects_invalid_bounds():
    log = bl.BattleLogRetainer()
    log.append("damage")
    with pytest.raises(bl.BattleLogError) as exc:
        log.export_range(3, 2)
    assert exc.value.code == "BATTLE_LOG_INVALID_REQUEST"
    with pytest.raises(bl.BattleLogError) as exc:
        log.export_range(0, 1)
    assert exc.value.code == "BATTLE_LOG_INVALID_REQUEST"
    assert [row["seq"] for row in log.export_range(1, 1)] == [1]


def test_entries_returns_detached_copies():
    log = bl.BattleLogRetainer(capacity=5)
    log.append("loot", {"item": "ring"})
    rows = log.entries()
    rows.clear()
    assert log.size() == 1
    rows = log.entries()
    rows[0].payload["item"] = "junk"
    assert log.entries()[0].payload == {"item": "ring"}


def test_snapshot_is_detached():
    log = bl.BattleLogRetainer(capacity=5)
    log.append("death", {"who": "adds"}, level=3)
    log.pin(1)
    state = log.snapshot()
    assert set(state) == {"schema", "entries", "attempts", "stats"}
    state["entries"].clear()
    state["stats"]["appended"] = 99
    assert log.size() == 1
    assert log.entries()[0].pinned is True
    assert log.stats()["appended"] == 1


def test_restore_roundtrip_preserves_pin_and_order():
    log = bl.BattleLogRetainer(capacity=3)
    log.append("death", {"n": 0}, level=4)
    log.append("damage", {"n": 1})
    log.pin(1)
    log.append("damage", {"n": 2})
    state = log.snapshot()

    clone = bl.BattleLogRetainer(capacity=3)
    clone.restore(copy.deepcopy(state))
    assert [row.seq for row in clone.entries()] == [1, 2, 3]
    clone.append("damage", {"n": 3})
    assert [row.seq for row in clone.entries()] == [1, 3, 4]


def test_restore_rejects_malformed_state():
    log = bl.BattleLogRetainer(capacity=3)
    log.append("death")
    bad_states = [
        None,
        {},
        {"schema": 2, "entries": [], "attempts": 0, "stats": {}},
        {"schema": 1, "entries": "x", "attempts": 0, "stats": {}},
        {"schema": 1, "entries": [], "attempts": 0, "stats": {"appended": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(bl.BattleLogError) as exc:
            log.restore(bad)
        assert exc.value.code == "BATTLE_LOG_STATE_INVALID"
    assert log.size() == 1


def test_concurrent_appends_produce_unique_sequences():
    log = bl.BattleLogRetainer(capacity=200)
    errors = []
    barrier = threading.Barrier(20)

    def worker(index):
        try:
            barrier.wait(timeout=5)
            log.append("damage", {"n": index})
        except Exception as exc:  # pragma: no cover - reported through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert sorted(row.seq for row in log.entries()) == list(range(1, 21))
    assert log.stats()["appended"] == 20
