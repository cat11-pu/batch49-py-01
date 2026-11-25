import pytest

import timed_quest_checkpoint as tq

STAGES = [("s1", 5000), ("s2", 3000), ("s3", 2000)]


def quest(**kwargs):
    return tq.TimedQuestCheckpoint(STAGES, **kwargs)


def test_enter_stage_starts_full_duration():
    q = quest()
    state = q.enter_stage("s1")
    assert state["chain_id"] == "chain"
    assert state["chain_state"] == "running"
    assert state["stage"] == "s1"
    assert state["stage_index"] == 0
    assert state["remaining_ms"] == 5000
    assert state["paused"] is False
    assert q.remaining_ms() == 5000
    assert q.paused_total_ms() == 0


def test_tick_reduces_remaining_by_elapsed_time():
    q = quest()
    q.enter_stage("s1")
    state = q.tick(1500)
    assert state["remaining_ms"] == 3500
    assert q.remaining_ms() == 3500


def test_tick_same_timestamp_does_not_double_deduct():
    q = quest()
    q.enter_stage("s1")
    q.tick(1000)
    q.tick(1000)
    assert q.remaining_ms() == 4000
    q.tick(1200)
    assert q.remaining_ms() == 3800


def test_tick_while_paused_does_not_advance():
    q = quest()
    q.enter_stage("s1")
    q.pause(500)
    state = q.tick(4000)
    assert state["paused"] is True
    assert state["remaining_ms"] == 4500


def test_resume_continues_from_frozen_remaining():
    q = quest()
    q.enter_stage("s1")
    q.pause(1000)
    state = q.resume(6000)
    assert state["paused"] is False
    assert state["remaining_ms"] == 4000
    q.tick(7000)
    assert q.remaining_ms() == 3000


def test_single_pause_accumulates_frozen_total():
    q = quest()
    q.enter_stage("s1")
    q.tick(2000)
    q.pause(2000)
    q.resume(5000)
    assert q.paused_total_ms() == 3000
    assert q.remaining_ms() == 3000


def test_enter_stage_advances_to_next_stage():
    q = quest()
    q.enter_stage("s1")
    q.tick(2000)
    state = q.enter_stage("s2")
    assert state["stage"] == "s2"
    assert state["stage_index"] == 1
    assert state["remaining_ms"] == 3000
    assert q.paused_total_ms() == 0


def test_enter_stage_rejects_out_of_order_and_repeat():
    q = quest()
    q.enter_stage("s1")
    with pytest.raises(tq.QuestTimerError) as excinfo:
        q.enter_stage("s3")
    assert excinfo.value.code == "STAGE_ORDER"
    with pytest.raises(tq.QuestTimerError) as excinfo:
        q.enter_stage("s1")
    assert excinfo.value.code == "STAGE_ACTIVE"


def test_enter_stage_rejects_while_paused():
    q = quest()
    q.enter_stage("s1")
    q.pause(100)
    with pytest.raises(tq.QuestTimerError) as excinfo:
        q.enter_stage("s2")
    assert excinfo.value.code == "QUEST_PAUSED"


def test_tick_past_deadline_rewinds_to_stage_entry():
    q = quest()
    q.enter_stage("s1")
    state = q.tick(5001)
    assert state["chain_state"] == "rewound"
    assert state["stage"] == "s1"
    assert state["stage_index"] == 0
    assert state["remaining_ms"] == 5000
    assert state["failures"] == 1


def test_explicit_checkpoint_restores_stage_timer():
    q = quest()
    q.enter_stage("s1")
    q.tick(1500)
    q.checkpoint()
    q.tick(1000)
    assert q.remaining_ms() == 2500
    state = q.fail_and_rewind()
    assert state["chain_state"] == "rewound"
    assert state["stage"] == "s1"
    assert state["remaining_ms"] == 5000


def test_fail_and_rewind_reports_reason_and_failure_count():
    q = quest()
    q.enter_stage("s1")
    q.tick(1000)
    out = q.fail_and_rewind("manual")
    assert out["reason"] == "manual"
    assert out["failures"] == 1
    q.tick(200)
    out = q.fail_and_rewind("manual_again")
    assert out["reason"] == "manual_again"
    assert out["failures"] == 2


def test_finish_chain_marks_completed():
    q = quest()
    q.enter_stage("s1")
    q.enter_stage("s2")
    q.enter_stage("s3")
    state = q.finish_chain()
    assert state["chain_state"] == "completed"
    assert state["stage"] is None
    with pytest.raises(tq.QuestTimerError) as excinfo:
        q.finish_chain()
    assert excinfo.value.code == "CHAIN_COMPLETED"


def test_grant_items_accumulate_by_kind():
    q = quest()
    q.grant_temp_item("rusty_key", 2)
    q.grant_temp_item("rusty_key", 1)
    q.grant_reward("gem", 4)
    inventory = q.inventory()
    assert inventory["temp"] == {"rusty_key": 3}
    assert inventory["rewards"] == {"gem": 4}


def test_snapshot_restore_roundtrip_and_invalid_state():
    q = quest()
    q.enter_stage("s1")
    q.tick(2000)
    saved = q.snapshot()
    q.tick(1000)
    assert q.remaining_ms() == 2000
    q.restore(saved)
    assert q.remaining_ms() == 3000
    assert q.state()["stage"] == "s1"
    with pytest.raises(tq.QuestTimerError) as excinfo:
        q.restore({"bogus": True})
    assert excinfo.value.code == "INVALID_SNAPSHOT"
    assert q.remaining_ms() == 3000
