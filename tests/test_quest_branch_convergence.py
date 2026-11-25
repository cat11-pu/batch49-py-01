import pytest

import quest_branch_convergence as qc

BRANCHES = {"left": ["l1", "l2"], "right": ["r1"]}
TARGETS = {"l1": 2, "l2": 1, "r1": 1}
REWARDS = {"l1": 10, "l2": 5, "r1": 7, "m1": 30}
MERGES = {"m1": ["left", "right"]}


def chain(**overrides):
    quest = qc.QuestBranchConvergence()
    params = {"branches": BRANCHES, "targets": TARGETS,
              "rewards": REWARDS, "merges": MERGES}
    params.update(overrides)
    quest.start_chain("c1", **params)
    return quest


def test_start_chain_registers_zero_progress():
    quest = chain()
    view = quest.progress("c1")
    assert view["chain_id"] == "c1"
    assert view["abandoned"] is False
    assert view["reward_total"] == 0
    assert view["branches"]["left"] == {
        "l1": {"progress": 0, "target": 2, "completed": False},
        "l2": {"progress": 0, "target": 1, "completed": False},
    }
    assert view["branches"]["right"]["r1"]["target"] == 1
    assert view["merges"] == {"m1": False}
    assert quest.stats()["chains"] == 1


def test_duplicate_chain_rejected():
    quest = chain()
    with pytest.raises(qc.QuestError) as info:
        quest.start_chain("c1", branches=BRANCHES, targets=TARGETS)
    assert info.value.code == "QUEST_DUPLICATE_CHAIN"
    assert quest.stats()["chains"] == 1


def test_unknown_node_rejected():
    quest = chain()
    with pytest.raises(qc.QuestError) as info:
        quest.advance_branch("c1", "left", "ghost", 1, "e1")
    assert info.value.code == "QUEST_UNKNOWN_NODE"
    with pytest.raises(qc.QuestError) as info:
        quest.advance_branch("c1", "left", "r1", 1, "e2")
    assert info.value.code == "QUEST_UNKNOWN_NODE"
    assert quest.progress("c1")["branches"]["left"]["l1"]["progress"] == 0


def test_out_of_order_rejected():
    quest = chain()
    with pytest.raises(qc.QuestError) as info:
        quest.advance_branch("c1", "left", "l2", 1, "e1")
    assert info.value.code == "QUEST_OUT_OF_ORDER"
    assert quest.progress("c1")["branches"]["left"]["l2"]["progress"] == 0


def test_advance_accumulates_and_completes():
    quest = chain()
    first = quest.advance_branch("c1", "left", "l1", 1, "e1")
    assert (first["progress"], first["completed"], first["granted"]) == (
        1, False, False)
    second = quest.advance_branch("c1", "left", "l1", 1, "e2")
    assert (second["progress"], second["completed"], second["granted"]) == (
        2, True, True)
    assert quest.reward_total("c1") == 10
    assert quest.stats()["grants"] == 1
    state = quest.node_state("c1", "l1")
    assert (state.node_id, state.progress, state.target) == ("l1", 2, 2)
    assert state.completed is True and state.reward_granted is True


def test_advance_caps_progress_at_target():
    quest = chain()
    outcome = quest.advance_branch("c1", "left", "l1", 5, "e1")
    assert outcome["progress"] == 2
    assert outcome["completed"] is True
    assert outcome["granted"] is True
    assert quest.progress("c1")["branches"]["left"]["l1"]["progress"] == 2
    assert quest.reward_total("c1") == 10
    assert quest.stats()["grants"] == 1


def test_duplicate_event_is_idempotent():
    quest = chain()
    first = quest.advance_branch("c1", "left", "l1", 2, "evt-1")
    second = quest.advance_branch("c1", "left", "l1", 2, "evt-1")
    assert second["progress"] == first["progress"] == 2
    assert second["completed"] is True
    assert quest.reward_total("c1") == 10
    assert quest.stats()["grants"] == 1
    assert quest.stats()["deduped"] == 1


def test_nonpositive_delta_rejected():
    quest = chain()
    for index, bad in enumerate((0, -2)):
        with pytest.raises(qc.QuestError) as info:
            quest.advance_branch("c1", "left", "l1", bad, f"bad-{index}")
        assert info.value.code == "QUEST_INVALID_DELTA"
    assert quest.progress("c1")["branches"]["left"]["l1"]["progress"] == 0
    assert quest.reward_total("c1") == 0


def test_progress_returns_deep_copy():
    quest = chain()
    view = quest.progress("c1")
    view["branches"]["left"]["l1"]["progress"] = 99
    view["reward_total"] = 99
    fresh = quest.progress("c1")
    assert fresh["branches"]["left"]["l1"]["progress"] == 0
    assert quest.reward_total("c1") == 0


def test_merge_gate_locks_until_all_branches_done():
    quest = chain()
    quest.advance_branch("c1", "left", "l1", 2, "e1")
    quest.advance_branch("c1", "left", "l2", 1, "e2")
    assert quest.check_merge("c1", "m1") is False
    with pytest.raises(qc.QuestError) as info:
        quest.unlock_merge("c1", "m1")
    assert info.value.code == "QUEST_MERGE_LOCKED"
    quest.advance_branch("c1", "right", "r1", 1, "e3")
    assert quest.check_merge("c1", "m1") is True
    assert quest.unlock_merge("c1", "m1") == 30
    assert quest.reward_total("c1") == 52
    assert quest.progress("c1")["merges"] == {"m1": True}
    with pytest.raises(qc.QuestError) as info:
        quest.unlock_merge("c1", "m1")
    assert info.value.code == "QUEST_MERGE_DUPLICATE"
    assert quest.stats()["merges"] == 1


def test_abandon_blocks_advance():
    quest = chain()
    quest.abandon_chain("c1")
    assert quest.progress("c1")["abandoned"] is True
    with pytest.raises(qc.QuestError) as info:
        quest.advance_branch("c1", "left", "l1", 1, "e1")
    assert info.value.code == "QUEST_ABANDONED"
    assert quest.stats()["abandoned"] == 1


def test_branches_track_progress_independently():
    quest = chain()
    quest.advance_branch("c1", "left", "l1", 1, "e1")
    view = quest.progress("c1")
    assert view["branches"]["left"]["l1"]["progress"] == 1
    assert view["branches"]["right"]["r1"]["progress"] == 0
    quest.advance_branch("c1", "right", "r1", 1, "e2")
    view = quest.progress("c1")
    assert view["branches"]["left"]["l1"]["completed"] is False
    assert view["branches"]["right"]["r1"]["completed"] is True
    assert quest.node_state("c1", "l1").completed is False


def test_unknown_merge_rejected():
    quest = chain()
    with pytest.raises(qc.QuestError) as info:
        quest.check_merge("c1", "ghost")
    assert info.value.code == "QUEST_UNKNOWN_MERGE"
    with pytest.raises(qc.QuestError) as info:
        quest.unlock_merge("c1", "ghost")
    assert info.value.code == "QUEST_UNKNOWN_MERGE"
    assert quest.progress("c1")["merges"] == {"m1": False}


def test_snapshot_restore_roundtrip():
    quest = chain()
    quest.advance_branch("c1", "left", "l1", 2, "e1")
    quest.advance_branch("c1", "left", "l2", 1, "e2")
    quest.advance_branch("c1", "right", "r1", 1, "e3")
    quest.unlock_merge("c1", "m1")
    state = quest.snapshot()
    clone = qc.QuestBranchConvergence()
    clone.restore(state)
    assert clone.progress("c1") == quest.progress("c1")
    assert clone.reward_total("c1") == 52
    assert clone.check_merge("c1", "m1") is True
    replay = clone.advance_branch("c1", "left", "l2", 1, "e2")
    assert replay["completed"] is True
    assert clone.stats()["deduped"] == 1
    quest.advance_branch("c1", "right", "r1", 1, "e4")
    assert clone.progress("c1")["branches"]["right"]["r1"]["progress"] == 1


def test_restore_rejects_bad_state():
    quest = chain()
    with pytest.raises(qc.QuestError) as info:
        quest.restore({"chains": []})
    assert info.value.code == "QUEST_INVALID_STATE"
    view = quest.progress("c1")
    assert view["reward_total"] == 0
    assert view["branches"]["left"]["l1"]["progress"] == 0
