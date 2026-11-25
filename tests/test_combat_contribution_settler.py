import pytest

import combat_contribution_settler as cs


def settler(**kwargs):
    return cs.CombatContributionSettler(**kwargs)


def settled_battle(*, boss_hp=1000, hits, **kwargs):
    s = settler(**kwargs)
    s.open_window("b1", boss_hp)
    for player_id, account_id, damage in hits:
        s.record_damage("b1", player_id, account_id, damage)
    s.close_window("b1")
    return s, s.settle("b1")


def test_open_window_returns_label_and_open_state():
    s = settler()
    window_id = s.open_window("b1", 1000)
    assert window_id == "b1:w1"
    assert s.window_state("b1") == "open"
    assert s.remaining_hp("b1") == 1000


def test_open_window_rejects_duplicate_and_bad_hp():
    s = settler()
    s.open_window("b1", 1200)
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.open_window("b1", 1200)
    assert excinfo.value.code == "WINDOW_ALREADY_OPEN"
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        settler().open_window("b2", 0)
    assert excinfo.value.code == "INVALID_BOSS_HP"


def test_record_damage_requires_open_window_and_positive_amount():
    s = settler()
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.record_damage("b1", "p1", "acc1", 100)
    assert excinfo.value.code == "WINDOW_NOT_OPEN"
    s.open_window("b1", 1000)
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.record_damage("b1", "p1", "acc1", 0)
    assert excinfo.value.code == "INVALID_DAMAGE"
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.record_damage("b1", "p1", "acc1", -5)
    assert excinfo.value.code == "INVALID_DAMAGE"


def test_close_window_twice_is_rejected():
    s = settler()
    s.open_window("b1", 1000)
    s.close_window("b1")
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.close_window("b1")
    assert excinfo.value.code == "WINDOW_CLOSED"


def test_close_window_aggregates_raw_damage_per_player():
    s = settler()
    s.open_window("b1", 1000)
    s.record_damage("b1", "p1", "acc1", 300)
    s.record_damage("b1", "p2", "acc1", 200)
    s.record_damage("b1", "p1", "acc1", 100)
    summary = s.close_window("b1")
    assert summary["raw_total"] == 600
    assert summary["players"]["p1"] == {"raw": 400, "hits": 2}
    assert summary["players"]["p2"] == {"raw": 200, "hits": 1}
    entries = s.ledger("b1")
    assert len(entries) == 3
    assert entries[0]["player_id"] == "p1"
    assert entries[0]["damage"] == 300
    assert entries[2]["damage"] == 100


def test_settle_requires_closed_window():
    s = settler()
    s.open_window("b1", 1000)
    s.record_damage("b1", "p1", "acc1", 100)
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.settle("b1")
    assert excinfo.value.code == "BATTLE_NOT_CLOSED"


def test_settle_ranks_accounts_by_damage():
    s, result = settled_battle(hits=[("p1", "acc1", 400), ("p2", "acc2", 500)])
    ranking = result["ranking"]
    assert [row["account_id"] for row in ranking] == ["acc2", "acc1"]
    assert [row["damage"] for row in ranking] == [500, 400]
    assert result["settled"] is True


def test_settle_is_idempotent():
    s, first = settled_battle(hits=[("p1", "acc1", 400), ("p2", "acc2", 600)])
    ledger_size = len(s.ledger("b1"))
    second = s.settle("b1")
    assert second == first
    assert len(s.ledger("b1")) == ledger_size
    assert s.stats()["battles_settled"] == 1


def test_settle_merges_characters_of_same_account():
    s, result = settled_battle(
        hits=[("p1", "acc1", 300), ("p2", "acc1", 200), ("p3", "acc2", 400)],
    )
    ranking = result["ranking"]
    assert ranking[0]["account_id"] == "acc1"
    assert ranking[0]["damage"] == 500
    assert ranking[0]["players"] == ("p1", "p2")
    assert ranking[1]["account_id"] == "acc2"
    assert ranking[1]["damage"] == 400


def test_settle_reports_overkill_and_zero_remaining_hp():
    s, result = settled_battle(hits=[("p1", "acc1", 700), ("p2", "acc2", 600)])
    assert result["remaining_hp"] == 0
    assert result["overkill_total"] == 300
    assert sum(row["damage"] for row in result["ranking"]) == 1000
    assert s.remaining_hp("b1") == 0


def test_eligibility_tiers_follow_share_of_top():
    s, _ = settled_battle(hits=[("p1", "acc1", 200), ("p2", "acc2", 500)])
    assert s.eligibility("b1") == {"acc2": "gold", "acc1": "silver"}


def test_eligibility_excludes_below_min_damage():
    s, _ = settled_battle(
        hits=[("p1", "acc1", 400), ("p2", "acc2", 300), ("p3", "acc3", 50)],
        min_damage=100,
    )
    tiers = s.eligibility("b1")
    assert "acc3" not in tiers
    assert tiers["acc1"] == "gold"


def test_settle_breaks_ties_by_account_id():
    s, result = settled_battle(hits=[("p1", "accB", 300), ("p2", "accA", 300)])
    assert [row["account_id"] for row in result["ranking"]] == ["accA", "accB"]


def test_snapshot_restore_roundtrip_and_invalid_state():
    s, _ = settled_battle(hits=[("p1", "acc1", 200), ("p2", "acc2", 500)])
    saved = s.snapshot()
    s.open_window("b2", 800)
    s.record_damage("b2", "p9", "acc9", 100)
    s.restore(saved)
    assert s.window_state("b1") == "closed"
    assert s.remaining_hp("b1") == 300
    assert s.eligibility("b1") == {"acc2": "gold", "acc1": "silver"}
    with pytest.raises(cs.CombatSettlementError) as excinfo:
        s.restore({"not": "a snapshot"})
    assert excinfo.value.code == "INVALID_SNAPSHOT"
    assert s.remaining_hp("b1") == 300


def test_stats_counts_window_lifecycle():
    s = settler()
    s.open_window("b1", 1000)
    s.record_damage("b1", "p1", "acc1", 300)
    s.record_damage("b1", "p2", "acc1", 200)
    s.close_window("b1")
    s.settle("b1")
    stats = s.stats()
    assert stats["windows_opened"] == 1
    assert stats["windows_closed"] == 1
    assert stats["battles_settled"] == 1
    assert stats["damage_entries"] == 2
