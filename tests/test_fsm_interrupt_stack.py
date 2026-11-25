import pytest

import fsm_interrupt_stack as fis


def make_stack(**kwargs):
    return fis.FsmInterruptStack(**kwargs)


def test_duplicate_state_definition_rejected():
    stack = make_stack()
    stack.define_state("patrol")
    with pytest.raises(fis.InterruptError) as err:
        stack.define_state("patrol", parent=None)
    assert err.value.code == "INTERRUPT_DUPLICATE_STATE"


def test_unknown_base_state_rejected():
    stack = make_stack()
    with pytest.raises(fis.InterruptError) as err:
        stack.set_base("ghost")
    assert err.value.code == "INTERRUPT_UNKNOWN_STATE"


def test_initial_base_state_and_clean_stack():
    stack = make_stack()
    stack.define_state("patrol")
    stack.set_base("patrol")
    assert stack.current() == "patrol"
    assert stack.stack_depth() == 0
    assert stack.progress() == 0


def test_idle_tick_advances_base_progress():
    stack = make_stack()
    stack.define_state("patrol")
    stack.set_base("patrol")
    result = stack.tick(3)
    assert result["expired"] == ()
    assert result["current"] == "patrol"
    assert result["depth"] == 0
    assert stack.progress() == 3


def test_push_interrupt_becomes_current_without_progress():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.set_base("patrol")
    result = stack.push_interrupt("hurt", source="hit", duration=2, priority=1)
    assert result["pushed"] is True
    assert result["depth"] == 1
    assert stack.current() == "hurt"
    assert stack.stack_depth() == 1
    assert stack.progress() == 0


def test_nested_pop_returns_to_previous_interrupt():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.define_state("stun")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=1)
    stack.push_interrupt("stun", source="stun_skill", duration=5, priority=2)
    assert stack.stack_depth() == 2
    assert stack.current() == "stun"
    result = stack.pop_interrupt()
    assert result["popped"] == "stun"
    assert result["current"] == "hurt"
    assert result["depth"] == 1


def test_pop_empty_stack_rejected():
    stack = make_stack()
    stack.define_state("patrol")
    stack.set_base("patrol")
    with pytest.raises(fis.InterruptError) as err:
        stack.pop_interrupt()
    assert err.value.code == "INTERRUPT_STACK_EMPTY"


def test_pop_with_mismatched_source_keeps_stack():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=1)
    with pytest.raises(fis.InterruptError) as err:
        stack.pop_interrupt(source="poison")
    assert err.value.code == "INTERRUPT_SOURCE_MISMATCH"
    assert stack.stack_depth() == 1
    assert stack.current() == "hurt"


def test_tick_freezes_progress_and_expires_one_layer():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=2, priority=1)
    first = stack.tick(1)
    assert first["expired"] == ()
    assert first["current"] == "hurt"
    assert stack.progress() == 0
    second = stack.tick(1)
    assert second["expired"] == ("hurt",)
    assert second["current"] == "patrol"
    assert stack.stack_depth() == 0
    stack.tick(1)
    assert stack.progress() == 1


def test_lower_priority_push_rejected_without_change():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.define_state("stun")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=5)
    result = stack.push_interrupt("stun", source="stun_skill", duration=5,
                                  priority=2)
    assert result["pushed"] is False
    assert result["reason"] == "INTERRUPT_PRIORITY_REJECTED"
    assert result["depth"] == 1
    assert stack.current() == "hurt"
    assert stack.stack_depth() == 1


def test_higher_priority_push_stacks_on_top():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.define_state("stun")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=1)
    result = stack.push_interrupt("stun", source="stun_skill", duration=5,
                                  priority=5)
    assert result["pushed"] is True
    assert result["depth"] == 2
    assert stack.current() == "stun"


def test_push_unknown_state_rejected():
    stack = make_stack()
    stack.define_state("patrol")
    stack.set_base("patrol")
    with pytest.raises(fis.InterruptError) as err:
        stack.push_interrupt("ghost", source="hit", duration=1)
    assert err.value.code == "INTERRUPT_UNKNOWN_STATE"


def test_cooldown_configuration_and_validation():
    stack = make_stack(default_cooldown=2)
    assert stack.cooldown_for("hit") == 2
    stack.set_cooldown("hit", 3)
    assert stack.cooldown_for("hit") == 3
    with pytest.raises(fis.InterruptError) as err:
        stack.set_cooldown("hit", -1)
    assert err.value.code == "INTERRUPT_INVALID_REQUEST"


def test_snapshot_restore_keeps_stack_and_remaining_duration():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=1)
    snapshot = stack.snapshot()
    stack.tick(2)
    stack.restore(snapshot)
    assert stack.current() == "hurt"
    assert stack.stack_depth() == 1
    stack.tick(4)
    assert stack.stack_depth() == 1
    result = stack.tick(1)
    assert result["expired"] == ("hurt",)
    assert stack.current() == "patrol"


def test_invalid_snapshot_restore_leaves_stack_untouched():
    stack = make_stack()
    stack.define_state("patrol")
    stack.define_state("hurt")
    stack.set_base("patrol")
    stack.push_interrupt("hurt", source="hit", duration=5, priority=1)
    with pytest.raises(fis.InterruptError) as err:
        stack.restore({"frames": "broken"})
    assert err.value.code == "INTERRUPT_SNAPSHOT_INVALID"
    assert stack.current() == "hurt"
    assert stack.stack_depth() == 1
