import pytest

import hidden_achievement_hints as ha

HINTS = ["门口有脚印", "军械库少一把刀", "守夜人换了班"]


def new(**kwargs):
    return ha.HiddenAchievementHints(**kwargs)


def test_register_and_total_hints():
    h = new()
    h.register_hidden("ACH_RAID", "初阵", HINTS, reward_id="REW_BANNER")
    assert h.total_hints("ACH_RAID") == 3
    with pytest.raises(ha.AchievementError) as ei:
        h.register_hidden("ACH_RAID", "重复", ["x"])
    assert ei.value.code == "ACH_DUPLICATE"


def test_register_rejects_bad_definition():
    h = new()
    with pytest.raises(ha.AchievementError) as ei:
        h.register_hidden("ACH_EMPTY", "空线索", [])
    assert ei.value.code == "ACH_INVALID"
    with pytest.raises(ha.AchievementError) as ei2:
        h.register_hidden("", "无名", ["a"])
    assert ei2.value.code == "ACH_INVALID"
    with pytest.raises(ha.AchievementError) as ei3:
        h.register_hidden("ACH_BLANK_HINT", "空白线索", ["a", "  "])
    assert ei3.value.code == "ACH_INVALID"


def test_register_capacity_limit():
    h = new(max_achievements=2)
    h.register_hidden("A1", "一", ["a"])
    h.register_hidden("A2", "二", ["b"])
    with pytest.raises(ha.AchievementError) as ei:
        h.register_hidden("A3", "三", ["c"])
    assert ei.value.code == "ACH_CAPACITY"
    assert h.total_hints("A1") == 1


def test_reveal_advances_one_stage():
    h = new()
    h.register_hidden("A", "一", HINTS)
    first = h.reveal_hint("acct", "A", 0)
    assert first["revealed"] is True
    assert first["stage"] == 0
    assert first["progress"] == 1
    second = h.reveal_hint("acct", "A", 1)
    assert second["revealed"] is True
    assert second["progress"] == 2
    assert h.progress_of("acct", "A") == 2


def test_reveal_same_stage_is_suppressed():
    h = new()
    h.register_hidden("A", "一", HINTS)
    h.reveal_hint("acct", "A", 0)
    again = h.reveal_hint("acct", "A", 0)
    assert again["revealed"] is False
    assert again["reason"] == "already"
    assert again["progress"] == 1
    assert h.stats("acct")["hints_revealed"] == 1


def test_reveal_rejects_bad_stage_and_unknown():
    h = new()
    h.register_hidden("A", "一", ["h0"])
    with pytest.raises(ha.AchievementError) as ei:
        h.reveal_hint("acct", "A", 5)
    assert ei.value.code == "ACH_BAD_STAGE"
    with pytest.raises(ha.AchievementError) as ei2:
        h.reveal_hint("acct", "A", -1)
    assert ei2.value.code == "ACH_BAD_STAGE"
    with pytest.raises(ha.AchievementError) as ei3:
        h.reveal_hint("acct", "NOPE", 0)
    assert ei3.value.code == "ACH_UNKNOWN"


def test_unlock_incomplete_keeps_state():
    h = new()
    h.register_hidden("A", "一", HINTS, reward_id="REW_BANNER")
    h.reveal_hint("acct", "A", 0)
    out = h.unlock("acct", "A")
    assert out["unlocked"] is False
    assert out["reward_id"] == ""
    assert h.is_achieved("acct", "A") is False
    assert h.stats("acct")["rewards_granted"] == 0


def test_unlock_grants_reward_once():
    h = new()
    h.register_hidden("A", "一", ["h0", "h1"], reward_id="REW_BANNER")
    h.reveal_hint("acct", "A", 0)
    h.reveal_hint("acct", "A", 1)
    first = h.unlock("acct", "A")
    assert first["unlocked"] is True
    assert first["reward_id"] == "REW_BANNER"
    assert first["reward_state"] == "granted"
    second = h.unlock("acct", "A")
    assert second["reward_state"] == "already_unlocked"
    assert h.stats("acct") == {
        "achieved": 1,
        "hints_revealed": 2,
        "rewards_granted": 1,
        "pending_rewards": 0,
        "skipped": 0,
    }


def test_unlock_rejects_unknown_and_blank_account():
    h = new()
    with pytest.raises(ha.AchievementError) as ei:
        h.unlock("acct", "NOPE")
    assert ei.value.code == "ACH_UNKNOWN"
    h.register_hidden("A", "一", ["h0"])
    with pytest.raises(ha.AchievementError) as ei2:
        h.unlock("", "A")
    assert ei2.value.code == "ACH_INVALID"


def test_visible_for_hides_unrevealed_entry():
    h = new()
    h.register_hidden("B", "乙", ["hb0"])
    h.register_hidden("A", "甲", ["ha0", "ha1"])
    h.reveal_hint("acct", "A", 0)
    views = h.visible_for("acct")
    assert [v.achievement_id for v in views] == ["A"]
    assert views[0].revealed_hints == ("ha0",)
    assert views[0].achieved is False
    assert views[0].total_hints == 2


def test_visible_for_shows_achieved_entry():
    h = new()
    h.register_hidden("A", "甲", ["ha0"], reward_id="REW_BANNER")
    h.reveal_hint("acct", "A", 0)
    h.unlock("acct", "A")
    views = h.visible_for("acct")
    assert len(views) == 1
    assert views[0].achieved is True
    assert views[0].revealed_hints == ("ha0",)
    assert views[0].reward_id == "REW_BANNER"


def test_pending_reward_reissue_flow():
    h = new()
    h.register_hidden("A", "甲", ["ha0"], reward_id="REW_BANNER",
                      reward_delay_seconds=3600)
    h.reveal_hint("acct", "A", 0)
    out = h.unlock("acct", "A")
    assert out["reward_state"] == "pending"
    assert h.pending_rewards("acct") == ["A"]
    assert h.stats("acct")["rewards_granted"] == 0
    reissued = h.reissue_reward("acct", "A")
    assert reissued["reissued"] is True
    assert reissued["reward_id"] == "REW_BANNER"
    assert h.pending_rewards("acct") == []
    assert h.stats("acct")["rewards_granted"] == 1
    again = h.reissue_reward("acct", "A")
    assert again["reissued"] is False
    assert again["reason"] == "nothing_pending"


def test_stats_are_per_account():
    h = new()
    h.register_hidden("A", "甲", ["ha0"])
    h.reveal_hint("one", "A", 0)
    assert h.stats("one")["hints_revealed"] == 1
    assert h.stats("two")["hints_revealed"] == 0
    assert h.stats("two")["achieved"] == 0
    assert h.progress_of("two", "A") == 0


def test_snapshot_restore_roundtrip():
    h = new()
    h.register_hidden("A", "甲", ["ha0", "ha1"], reward_id="REW_BANNER")
    h.reveal_hint("acct", "A", 0)
    snap = h.snapshot()
    h.reveal_hint("acct", "A", 1)
    h.unlock("acct", "A")
    assert h.progress_of("acct", "A") == 2
    assert h.is_achieved("acct", "A") is True
    h.restore(snap)
    assert h.progress_of("acct", "A") == 1
    assert h.is_achieved("acct", "A") is False
    assert h.snapshot() == snap


def test_restore_rejects_malformed_state():
    h = new()
    h.register_hidden("A", "甲", ["ha0"])
    h.reveal_hint("acct", "A", 0)
    with pytest.raises(ha.AchievementError) as ei:
        h.restore({"achievements": []})
    assert ei.value.code == "ACH_SNAPSHOT_INVALID"
    assert h.progress_of("acct", "A") == 1
