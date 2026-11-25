import pytest

import hierarchical_fsm as hf


def test_duplicate_state_definition_rejected():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    with pytest.raises(hf.FsmError) as err:
        fsm.define_state("idle", priority=2)
    assert err.value.code == "FSM_DUPLICATE_STATE"


def test_definition_with_missing_parent_rejected():
    fsm = hf.HierarchicalStateMachine()
    with pytest.raises(hf.FsmError) as err:
        fsm.define_state("idle", parent="missing")
    assert err.value.code == "FSM_UNKNOWN_STATE"


def test_initial_nested_state_enters_ancestors_in_order():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("combat")
    fsm.define_state("windup", parent="combat")
    fsm.define_state("swing", parent="windup")
    log = []
    fsm.register_hooks("combat", on_enter=lambda: log.append("enter:combat"))
    fsm.register_hooks("windup", on_enter=lambda: log.append("enter:windup"))
    fsm.register_hooks("swing", on_enter=lambda: log.append("enter:swing"))
    fsm.set_initial("swing")
    assert log == ["enter:combat", "enter:windup", "enter:swing"]
    assert fsm.current_path() == ("root", "combat", "windup", "swing")


def test_current_state_and_path_for_top_level_state():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.set_initial("idle")
    assert fsm.current_state() == "idle"
    assert fsm.current_path() == ("root", "idle")


def test_transition_to_descendant_enters_only_missing_child():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("combat")
    fsm.define_state("windup", parent="combat")
    fsm.set_initial("combat")
    log = []
    fsm.register_hooks("windup", on_enter=lambda: log.append("enter:windup"))
    fsm.request_transition("windup", reason="engage")
    result = fsm.tick()
    assert result["executed"] is True
    assert log == ["enter:windup"]
    assert fsm.current_path() == ("root", "combat", "windup")


def test_transition_to_sibling_exits_then_enters_in_order():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("combat")
    fsm.define_state("windup", parent="combat")
    fsm.define_state("swing", parent="combat")
    fsm.set_initial("windup")
    log = []
    fsm.register_hooks("windup", on_exit=lambda: log.append("exit:windup"))
    fsm.register_hooks("swing", on_enter=lambda: log.append("enter:swing"))
    fsm.request_transition("swing")
    fsm.tick()
    assert log == ["exit:windup", "enter:swing"]
    assert fsm.current_path() == ("root", "combat", "swing")


def test_transition_to_ancestor_exits_child_only():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("combat")
    fsm.define_state("windup", parent="combat")
    fsm.define_state("swing", parent="windup")
    fsm.set_initial("swing")
    log = []
    fsm.register_hooks("swing", on_exit=lambda: log.append("exit:swing"))
    fsm.register_hooks("combat", on_enter=lambda: log.append("enter:combat"))
    fsm.request_transition("combat")
    fsm.tick()
    assert log == ["exit:swing"]
    assert fsm.current_path() == ("root", "combat")


def test_guard_rejection_keeps_path_and_reports_reason():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.define_state("stunned", guard=lambda: False)
    fsm.set_initial("idle")
    fsm.request_transition("stunned", priority=1, reason="target_locked")
    result = fsm.tick()
    assert result["executed"] is False
    assert result["rejected"] == "guard_denied"
    assert result["reason"] == "target_locked"
    assert result["from"] == result["to"] == ("root", "idle")
    assert fsm.current_path() == ("root", "idle")
    assert fsm.stats()["rejected"] == 1


def test_pending_queue_cap_rejects_new_request():
    fsm = hf.HierarchicalStateMachine(max_pending=1)
    fsm.define_state("idle")
    fsm.define_state("a")
    fsm.define_state("b")
    fsm.set_initial("idle")
    assert fsm.request_transition("a") is True
    with pytest.raises(hf.FsmError) as err:
        fsm.request_transition("b")
    assert err.value.code == "FSM_QUEUE_FULL"


def test_register_hooks_for_unknown_state_rejected():
    fsm = hf.HierarchicalStateMachine()
    with pytest.raises(hf.FsmError) as err:
        fsm.register_hooks("ghost", on_enter=lambda: None)
    assert err.value.code == "FSM_UNKNOWN_STATE"


def test_snapshot_restore_returns_to_captured_path_and_history():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.define_state("combat")
    fsm.set_initial("idle")
    snapshot = fsm.snapshot()
    fsm.request_transition("combat")
    fsm.tick()
    assert fsm.current_path() == ("root", "combat")
    fsm.restore(snapshot)
    assert fsm.current_path() == ("root", "idle")
    assert fsm.history() == ()


def test_invalid_snapshot_restore_leaves_state_untouched():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.set_initial("idle")
    with pytest.raises(hf.FsmError) as err:
        fsm.restore({"states": []})
    assert err.value.code == "FSM_SNAPSHOT_INVALID"
    assert fsm.current_path() == ("root", "idle")
    assert fsm.history() == ()


def test_tick_with_empty_queue_is_noop():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.set_initial("idle")
    result = fsm.tick()
    assert result["executed"] is False
    assert result["from"] == ("root", "idle")
    assert result["to"] == ("root", "idle")
    assert result["discarded"] == 0
    assert result["rejected"] is None
    assert fsm.history() == ()


def test_history_and_stats_record_executed_transition():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    fsm.define_state("combat")
    fsm.set_initial("idle")
    fsm.request_transition("combat", priority=5, reason="engage")
    result = fsm.tick()
    assert result["executed"] is True
    assert result["discarded"] == 0
    assert fsm.history() == (
        {
            "from": ("root", "idle"),
            "to": ("root", "combat"),
            "reason": "engage",
            "discarded": 0,
        },
    )
    assert fsm.stats() == {
        "transitions": 1,
        "rejected": 0,
        "queued": 1,
        "discarded": 0,
        "hook_errors": 0,
    }


def test_register_hooks_replaces_previous_callbacks():
    fsm = hf.HierarchicalStateMachine()
    fsm.define_state("idle")
    log = []
    fsm.register_hooks("idle", on_enter=lambda: log.append("first"))
    fsm.register_hooks("idle", on_enter=lambda: log.append("second"))
    fsm.set_initial("idle")
    assert log == ["second"]
