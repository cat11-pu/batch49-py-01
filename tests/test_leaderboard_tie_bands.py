import copy

import pytest

import leaderboard_tie_bands as lb

NOW = "2026-11-25T12:00:00+00:00"


def board(**kwargs):
    return lb.LeaderboardTieBands(**kwargs)


def fill(instance, scores, season="s1"):
    for index, score in enumerate(scores, start=1):
        instance.submit(season=season, player=f"p{index}", score=score,
                        achieved_at=f"2026-11-25T10:{index:02d}:00+00:00")


def test_submit_creates_entry_and_rank():
    instance = board()
    result = instance.submit(season="s1", player="p1", score=100,
                             achieved_at="2026-11-25T10:00:00+00:00")
    assert result.accepted is True
    assert result.improved is True
    assert result.best_score == 100
    assert result.rank == 1
    assert instance.score_of(season="s1", player="p1") == 100
    assert instance.count("s1") == 1
    assert instance.stats()["submissions"] == 1


def test_lower_score_keeps_best_and_is_not_improvement():
    instance = board()
    instance.submit(season="s1", player="p1", score=100,
                    achieved_at="2026-11-25T10:00:00+00:00")
    result = instance.submit(season="s1", player="p1", score=80,
                             achieved_at="2026-11-25T11:00:00+00:00")
    assert result.accepted is True
    assert result.improved is False
    assert result.best_score == 100
    assert result.rank == 1
    assert instance.top(season="s1", limit=10)[0].score == 100
    assert instance.stats()["improvements"] == 1


def test_equal_score_keeps_earlier_achievement():
    instance = board()
    instance.submit(season="s1", player="p1", score=100,
                    achieved_at="2026-11-25T10:00:00+00:00")
    result = instance.submit(season="s1", player="p1", score=100,
                             achieved_at="2026-11-25T11:00:00+00:00")
    assert result.improved is False
    entry = instance.top(season="s1", limit=1)[0]
    assert entry.score == 100
    assert entry.achieved_at == "2026-11-25T10:00:00+00:00"
    assert entry.seq == 1


def test_dense_rank_is_shared_by_tied_players():
    instance = board()
    instance.submit(season="s1", player="p1", score=100,
                    achieved_at="2026-11-25T10:00:00+00:00")
    instance.submit(season="s1", player="p2", score=100,
                    achieved_at="2026-11-25T10:01:00+00:00")
    instance.submit(season="s1", player="p3", score=50,
                    achieved_at="2026-11-25T10:02:00+00:00")
    assert instance.rank_of(season="s1", player="p1") == 1
    assert instance.rank_of(season="s1", player="p2") == 1
    assert instance.rank_of(season="s1", player="p3") == 2


def test_top_orders_by_score_and_respects_limit():
    instance = board()
    instance.submit(season="s1", player="p1", score=90,
                    achieved_at="2026-11-25T10:00:00+00:00")
    instance.submit(season="s1", player="p2", score=100,
                    achieved_at="2026-11-25T10:01:00+00:00")
    instance.submit(season="s1", player="p3", score=80,
                    achieved_at="2026-11-25T10:02:00+00:00")
    assert [entry.player for entry in instance.top(season="s1", limit=2)] == [
        "p2", "p1"]
    assert [entry.score for entry in instance.top(season="s1", limit=10)] == [
        100, 90, 80]


def test_top_rejects_non_positive_limit():
    instance = board()
    fill(instance, [100, 90])
    for limit in (0, -3):
        with pytest.raises(lb.LeaderboardError) as exc:
            instance.top(season="s1", limit=limit)
        assert exc.value.code == "LEADERBOARD_INVALID_LIMIT"


def test_unknown_player_has_no_rank():
    instance = board()
    fill(instance, [100])
    with pytest.raises(lb.LeaderboardError) as exc:
        instance.rank_of(season="s1", player="ghost")
    assert exc.value.code == "LEADERBOARD_UNKNOWN_PLAYER"
    assert instance.score_of(season="s1", player="ghost") is None


def test_default_bands_split_ten_players_by_ratio():
    instance = board()
    fill(instance, list(range(110, 100, -1)))
    rows = instance.bands(season="s1")
    assert [(row.name, row.min_rank, row.max_rank, row.reward)
            for row in rows] == [
        ("S", 1, 1, "GOLD"),
        ("A", 2, 3, "SILVER"),
        ("B", 4, 6, "BRONZE"),
        ("C", 7, 10, "RIBBON"),
    ]


def test_recompute_bands_uses_new_ratios():
    instance = board()
    fill(instance, [100, 90])
    rows = instance.recompute_bands(
        season="s1", ratios=[("TOP", 0.5, "GOLD"), ("REST", 1.0, "SILVER")])
    assert [(row.name, row.min_rank, row.max_rank) for row in rows] == [
        ("TOP", 1, 1), ("REST", 2, 2)]
    assert instance.bands(season="s1") == rows
    assert instance.stats()["band_recomputes"] == 1


def test_invalid_ratio_lists_are_rejected():
    instance = board()
    fill(instance, [100, 90])
    bad_ratios = [
        [("A", 0.5, "GOLD")],
        [("A", 0.6, "GOLD"), ("B", 0.4, "SILVER")],
        [("A", 0.0, "GOLD"), ("B", 1.0, "SILVER")],
        [("A", 1.2, "GOLD")],
    ]
    for ratios in bad_ratios:
        with pytest.raises(lb.LeaderboardError) as exc:
            instance.recompute_bands(season="s1", ratios=ratios)
        assert exc.value.code == "LEADERBOARD_INVALID_BANDS"


def test_settlement_freezes_entries_and_pays_rewards():
    instance = board()
    fill(instance, list(range(110, 100, -1)))
    settlement = instance.settle(season="s1", now=NOW)
    assert settlement.settlement_id == 1
    assert settlement.season == "s1"
    assert settlement.created_at == NOW
    assert len(settlement.entries) == 10
    assert settlement.entries[0].player == "p1"
    assert [band.name for band in settlement.bands] == ["S", "A", "B", "C"]
    assert instance.settlement(1) == settlement
    rewards = instance.rewards(1)
    assert rewards["p1"] == "GOLD"
    assert rewards["p2"] == "SILVER"
    assert rewards["p3"] == "SILVER"
    assert rewards["p4"] == "BRONZE"
    assert rewards["p6"] == "BRONZE"
    assert rewards["p7"] == "RIBBON"
    assert rewards["p10"] == "RIBBON"


def test_unknown_settlement_is_rejected():
    instance = board()
    fill(instance, [100])
    with pytest.raises(lb.LeaderboardError) as exc:
        instance.settlement(99)
    assert exc.value.code == "LEADERBOARD_UNKNOWN_SETTLEMENT"
    with pytest.raises(lb.LeaderboardError) as exc:
        instance.rewards(99)
    assert exc.value.code == "LEADERBOARD_UNKNOWN_SETTLEMENT"


def test_snapshot_is_detached_and_restore_roundtrips():
    instance = board()
    fill(instance, list(range(110, 100, -1)))
    instance.settle(season="s1", now=NOW)
    state = instance.snapshot()
    assert set(state) == {"version", "seasons", "settlements", "ratios",
                          "stats", "next_settlement_id"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["settlements"].clear()
    state["seasons"].clear()
    assert instance.rewards(1)["p1"] == "GOLD"
    assert instance.count("s1") == 10

    clone = board()
    clone.restore(pristine)
    assert clone.rewards(1)["p7"] == "RIBBON"
    assert clone.score_of(season="s1", player="p3") == 108
    clone.submit(season="s1", player="p11", score=105,
                 achieved_at="2026-11-25T11:00:00+00:00")
    assert clone.count("s1") == 11


def test_restore_rejects_malformed_state_without_touching_data():
    instance = board()
    fill(instance, [100, 90])
    good = instance.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, seasons=[]),
        {key: value for key, value in good.items() if key != "stats"},
    ]
    for bad in bad_states:
        with pytest.raises(lb.LeaderboardError) as exc:
            instance.restore(bad)
        assert exc.value.code == "LEADERBOARD_SNAPSHOT_INVALID"
    assert instance.score_of(season="s1", player="p1") == 100
    assert instance.count("s1") == 2


def test_stats_track_submissions_improvements_and_settlements():
    instance = board()
    instance.submit(season="s1", player="p1", score=100,
                    achieved_at="2026-11-25T10:00:00+00:00")
    instance.submit(season="s1", player="p1", score=90,
                    achieved_at="2026-11-25T11:00:00+00:00")
    instance.settle(season="s1", now=NOW)
    instance.recompute_bands(
        season="s1", ratios=[("TOP", 0.5, "GOLD"), ("REST", 1.0, "SILVER")])
    assert instance.stats() == {"submissions": 2, "improvements": 1,
                                "settlements": 1, "band_recomputes": 1}
