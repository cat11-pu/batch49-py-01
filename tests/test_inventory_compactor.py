import pytest

import inventory_compactor as ic

DEFAULT_LIMIT = 99


def make(slots, priority, **kwargs):
    compactor = ic.InventoryCompactor(**kwargs)
    compactor.set_layout(slots)
    compactor.set_priority(priority)
    return compactor


def simulate(slots, moves, limits=None):
    """Apply moves one by one, asserting each step is legal."""
    limits = limits or {}
    state = {index: [stack.item_id, stack.quality, stack.quantity]
             for index, stack in slots.items()}
    for move in moves:
        assert move.from_slot != move.to_slot
        assert move.from_slot in state, f"source slot {move.from_slot} is empty"
        source = state[move.from_slot]
        limit = limits.get(source[0], DEFAULT_LIMIT)
        assert 0 < move.quantity <= source[2]
        target = state.get(move.to_slot)
        if target is None:
            state[move.to_slot] = [source[0], source[1], move.quantity]
        else:
            assert (target[0], target[1]) == (source[0], source[1])
            assert target[2] + move.quantity <= limit
            target[2] += move.quantity
        source[2] -= move.quantity
        if source[2] == 0:
            del state[move.from_slot]
    return {index: ic.Stack(item, quality, quantity)
            for index, (item, quality, quantity) in state.items()}


def test_plan_merges_same_group_into_lowest_slot():
    slots = {0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}
    comp = make(slots, ["A"])
    plan = comp.plan_moves()
    assert plan == [ic.Move(2, 0, 4)]
    assert comp.layout() == slots
    assert comp.stats()["plans"] == 1


def test_compact_applies_and_reports():
    comp = make({0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}, ["A"])
    summary = comp.compact()
    assert summary == {"moves": 1, "layout": {0: ic.Stack("A", 1, 7)}}
    assert comp.layout() == {0: ic.Stack("A", 1, 7)}
    assert comp.stats()["moves_applied"] == 1


def test_priority_orders_groups():
    slots = {0: ic.Stack("A", 1, 2), 1: ic.Stack("B", 1, 1)}
    comp = make(slots, ["B", "A"], slot_count=4)
    plan = comp.plan_moves()
    assert len(plan) == 3
    simulated = simulate(slots, plan)
    comp.compact()
    assert comp.layout() == {0: ic.Stack("B", 1, 1), 1: ic.Stack("A", 1, 2)}
    assert simulated == comp.layout()


def test_moves_are_legal_step_by_step():
    slots = {0: ic.Stack("A", 1, 2), 1: ic.Stack("B", 1, 1),
             3: ic.Stack("C", 2, 3)}
    comp = make(slots, ["C", "B", "A"], slot_count=4)
    plan = comp.plan_moves()
    assert len(plan) == 2
    assert simulate(slots, plan) == {
        0: ic.Stack("C", 2, 3),
        1: ic.Stack("B", 1, 1),
        2: ic.Stack("A", 1, 2),
    }


def test_apply_is_atomic_on_illegal_first_move():
    slots = {0: ic.Stack("A", 1, 1), 1: ic.Stack("B", 1, 1)}
    comp = make(slots, ["A", "B"])
    with pytest.raises(ic.CompactorError) as info:
        comp.apply_moves([ic.Move(0, 1, 1)])
    assert info.value.code == "COMPACT_ILLEGAL_MOVE"
    assert comp.layout() == slots
    assert comp.stats()["moves_applied"] == 0


def test_apply_rejects_empty_source():
    slots = {0: ic.Stack("A", 1, 1)}
    comp = make(slots, ["A"])
    with pytest.raises(ic.CompactorError) as info:
        comp.apply_moves([ic.Move(3, 0, 1)])
    assert info.value.code == "COMPACT_ILLEGAL_MOVE"
    assert comp.layout() == slots


def test_stack_limit_splits_group_across_slots():
    slots = {0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}
    comp = make(slots, ["A"])
    comp.set_stack_limit("A", 5)
    plan = comp.plan_moves()
    assert len(plan) == 2
    assert {(move.from_slot, move.to_slot, move.quantity)
            for move in plan} == {(2, 0, 2), (2, 1, 2)}
    assert simulate(slots, plan, {"A": 5}) == {
        0: ic.Stack("A", 1, 5),
        1: ic.Stack("A", 1, 2),
    }
    comp.compact()
    assert comp.layout() == {0: ic.Stack("A", 1, 5), 1: ic.Stack("A", 1, 2)}


def test_move_budget_rejects_swap():
    slots = {0: ic.Stack("A", 1, 2), 1: ic.Stack("B", 1, 1)}
    comp = make(slots, ["B", "A"], max_moves_per_item=1)
    with pytest.raises(ic.CompactorError) as info:
        comp.plan_moves()
    assert info.value.code == "COMPACT_BUDGET"
    assert comp.layout() == slots


def test_abort_clears_pending_plan():
    slots = {0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}
    comp = make(slots, ["A"])
    comp.plan_moves()
    comp.abort_plan()
    with pytest.raises(ic.CompactorError) as info:
        comp.apply_moves()
    assert info.value.code == "COMPACT_NO_PLAN"
    assert comp.layout() == slots
    assert comp.stats()["aborts"] == 1


def test_plan_is_deterministic_and_read_only():
    slots = {0: ic.Stack("A", 1, 2), 1: ic.Stack("B", 1, 1)}
    comp = make(slots, ["B", "A"], slot_count=4)
    first = comp.plan_moves()
    assert comp.layout() == slots
    second = comp.plan_moves()
    assert first == second
    assert comp.layout() == slots


def test_conservation_holds_and_catches_loss():
    slots = {0: ic.Stack("A", 1, 3), 1: ic.Stack("B", 2, 1),
             2: ic.Stack("A", 1, 4)}
    comp = make(slots, ["A", "B"])
    before = comp.layout()
    comp.compact()
    after = comp.layout()
    assert after == {0: ic.Stack("A", 1, 7), 1: ic.Stack("B", 2, 1)}
    assert comp.verify_conservation(before, after) is True
    tampered = dict(after)
    tampered[0] = ic.Stack("A", 1, 6)
    assert comp.verify_conservation(before, tampered) is False


def test_invalid_layout_rejected():
    comp = ic.InventoryCompactor(slot_count=4)
    for bad in ({0: ic.Stack("A", 1, 0)},
                {9: ic.Stack("A", 1, 1)},
                {0: ic.Stack("", 1, 1)}):
        with pytest.raises(ic.CompactorError) as info:
            comp.set_layout(bad)
        assert info.value.code == "COMPACT_INVALID_LAYOUT"
    assert comp.layout() == {}


def test_snapshot_restore_roundtrip():
    slots = {0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}
    comp = make(slots, ["A"])
    comp.set_stack_limit("A", 5)
    state = comp.snapshot()
    clone = ic.InventoryCompactor()
    clone.restore(state)
    assert clone.layout() == slots
    assert clone.plan_moves() == comp.plan_moves()
    assert clone.stats()["plans"] == 1
    comp.abort_plan()
    assert clone.layout() == slots


def test_restore_rejects_bad_state():
    slots = {0: ic.Stack("A", 1, 3)}
    comp = make(slots, ["A"])
    with pytest.raises(ic.CompactorError) as info:
        comp.restore({"layout": []})
    assert info.value.code == "COMPACT_SNAPSHOT_INVALID"
    assert comp.layout() == slots


def test_stats_counts_activity():
    comp = make({0: ic.Stack("A", 1, 3), 2: ic.Stack("A", 1, 4)}, ["A"])
    comp.compact()
    comp.plan_moves()
    comp.abort_plan()
    assert comp.stats() == {"plans": 2, "moves_applied": 1, "aborts": 1}
