import copy

import pytest

import rolling_leaderboard as rl


def board(**kwargs):
    return rl.RollingLeaderboard(**kwargs)


def submit(instance, player="p1", score=10, achieved_at="2026-11-25T10:00:00+00:00"):
    return instance.submit(player=player, score=score, achieved_at=achieved_at)


def test_submit_reports_day_window_and_first_best():
    instance = board()
    outcome = submit(instance, score=42)
    assert outcome.window == "2026-11-25"
    assert outcome.improved is True
    assert outcome.best == 42
    assert instance.best_of(player="p1", board="day",
                            window="2026-11-25") == 42
    assert instance.stats()["submitted"] == 1


def test_best_of_keeps_maximum_within_window():
    instance = board()
    submit(instance, score=10)
    lower = submit(instance, score=8)
    higher = submit(instance, score=15)
    assert lower.improved is False
    assert lower.best == 10
    assert higher.improved is True
    assert higher.best == 15
    assert instance.best_of(player="p1", board="day",
                            window="2026-11-25") == 15
    assert instance.stats()["improved"] == 2


def test_day_window_changes_after_midnight():
    instance = board()
    assert instance.window_of(achieved_at="2026-11-25T23:59:59+00:00",
                              board="day") == "2026-11-25"
    assert instance.window_of(achieved_at="2026-11-26T00:00:01+00:00",
                              board="day") == "2026-11-26"
    assert instance.window_of(achieved_at="2026-11-26T12:00:00+00:00",
                              board="day") == "2026-11-26"


def test_week_window_uses_iso_week():
    instance = board()
    assert instance.window_of(achieved_at="2026-11-23T00:00:00+00:00",
                              board="week") == "2026-W48"
    assert instance.window_of(achieved_at="2026-11-29T23:00:00+00:00",
                              board="week") == "2026-W48"
    assert instance.window_of(achieved_at="2026-11-30T09:00:00+00:00",
                              board="week") == "2026-W49"


def test_best_of_unknown_window_is_none():
    instance = board()
    assert instance.best_of(player="nobody", board="day",
                            window="2026-11-25") is None
    submit(instance, score=10)
    assert instance.best_of(player="p1", board="week", window="2026-W47") is None


def test_top_orders_scores_and_respects_limit():
    instance = board()
    submit(instance, player="p1", score=10,
           achieved_at="2026-11-25T10:00:00+00:00")
    submit(instance, player="p2", score=30,
           achieved_at="2026-11-25T10:01:00+00:00")
    submit(instance, player="p3", score=20,
           achieved_at="2026-11-25T10:02:00+00:00")
    rows = instance.top(board="day", window="2026-11-25", limit=2)
    assert [(row.player, row.score) for row in rows] == [("p2", 30), ("p3", 20)]
    assert len(instance.top(board="day", window="2026-11-25", limit=10)) == 3


def test_top_rejects_non_positive_limit():
    instance = board()
    submit(instance, score=10)
    for limit in (0, -1):
        with pytest.raises(rl.RollingError) as exc:
            instance.top(board="day", window="2026-11-25", limit=limit)
        assert exc.value.code == "ROLLING_INVALID_LIMIT"


def test_unknown_board_is_rejected():
    instance = board()
    for call in (
        lambda: instance.window_of(achieved_at="2026-11-25T10:00:00+00:00",
                                   board="month"),
        lambda: instance.best_of(player="p1", board="month",
                                 window="2026-11"),
        lambda: instance.top(board="month", window="2026-11", limit=3),
    ):
        with pytest.raises(rl.RollingError) as exc:
            call()
        assert exc.value.code == "ROLLING_INVALID_BOARD"


def test_roll_window_settles_then_clears_expired_windows():
    instance = board()
    submit(instance, player="p1", score=50,
           achieved_at="2026-11-23T10:00:00+00:00")
    submit(instance, player="p2", score=60,
           achieved_at="2026-11-24T10:00:00+00:00")
    results = instance.roll_window(board="day", now="2026-11-25T08:00:00+00:00")
    assert [(row.window, row.winner, row.best_score, row.entries)
            for row in results] == [
        ("2026-11-23", "p1", 50, 1),
        ("2026-11-24", "p2", 60, 1),
    ]
    assert instance.top(board="day", window="2026-11-23", limit=10) == []
    assert instance.stats()["rolled"] == 2


def test_purge_expired_removes_only_past_windows_and_is_idempotent():
    instance = board()
    submit(instance, score=10, achieved_at="2026-11-23T10:00:00+00:00")
    assert instance.purge_expired(now="2026-11-24T08:00:00+00:00") == 1
    assert instance.purge_expired(now="2026-11-24T08:00:00+00:00") == 0
    assert instance.top(board="day", window="2026-11-23", limit=10) == []
    assert instance.stats()["purged"] == 1


def test_history_best_tracks_across_windows():
    instance = board()
    submit(instance, score=50, achieved_at="2026-11-23T10:00:00+00:00")
    submit(instance, score=70, achieved_at="2026-11-24T10:00:00+00:00")
    submit(instance, score=60, achieved_at="2026-11-24T11:00:00+00:00")
    assert instance.history_best("p1") == 70
    assert instance.history_best("ghost") is None


def test_entry_capacity_cap_is_enforced_per_board():
    instance = board(max_entries=2)
    submit(instance, player="p1", score=10)
    submit(instance, player="p2", score=20)
    with pytest.raises(rl.RollingError) as exc:
        submit(instance, player="p3", score=30)
    assert exc.value.code == "ROLLING_CAPACITY_EXCEEDED"
    assert instance.stats()["submitted"] == 2
    assert instance.best_of(player="p3", board="day",
                            window="2026-11-25") is None


def test_snapshot_is_detached_and_restore_roundtrips():
    instance = board()
    submit(instance, score=50, achieved_at="2026-11-23T10:00:00+00:00")
    state = instance.snapshot()
    assert set(state) == {"version", "entries", "history", "stats", "next_seq"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["entries"].clear()
    state["stats"]["submitted"] = 999
    assert instance.best_of(player="p1", board="day",
                            window="2026-11-23") == 50

    clone = board()
    clone.restore(pristine)
    assert clone.best_of(player="p1", board="day",
                         window="2026-11-23") == 50
    assert clone.history_best("p1") == 50
    submit(clone, player="p2", score=80)
    assert clone.best_of(player="p2", board="day",
                         window="2026-11-25") == 80


def test_restore_rejects_malformed_state_without_touching_data():
    instance = board()
    submit(instance, score=10)
    good = instance.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, entries=[]),
        {key: value for key, value in good.items() if key != "history"},
    ]
    for bad in bad_states:
        with pytest.raises(rl.RollingError) as exc:
            instance.restore(bad)
        assert exc.value.code == "ROLLING_SNAPSHOT_INVALID"
    assert instance.best_of(player="p1", board="day",
                            window="2026-11-25") == 10


def test_stats_count_submissions_improvements_and_rolls():
    instance = board()
    submit(instance, score=10)
    submit(instance, score=20)
    submit(instance, score=5)
    assert instance.stats() == {"submitted": 3, "improved": 2,
                                "rolled": 0, "purged": 0}
