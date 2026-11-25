import copy

import pytest

import achievement_recompute_engine as ar

T1 = "2026-11-20T10:00:00+00:00"
T2 = "2026-11-20T11:00:00+00:00"
NOW = "2026-11-25T12:00:00+00:00"


def engine(batch_size=100):
    return ar.AchievementRecomputeEngine(batch_size=batch_size)


def add_rule(eng, rule_id="slayer", version=1, metric="kills", threshold=5,
             reward="TITLE-SLAYER", window_seconds=None):
    return eng.register_rule(rule_id, version=version, metric=metric,
                             threshold=threshold, reward=reward,
                             window_seconds=window_seconds)


def test_register_rule_validates_and_rejects_duplicate_version():
    eng = engine()
    bad = [
        dict(version=0, metric="kills", threshold=1, reward="R"),
        dict(version=1, metric="", threshold=1, reward="R"),
        dict(version=1, metric="kills", threshold=0, reward="R"),
        dict(version=1, metric="kills", threshold=1, reward=""),
        dict(version=1, metric="kills", threshold=1, reward="R",
             window_seconds=0),
    ]
    for kwargs in bad:
        with pytest.raises(ar.AchievementError) as exc:
            eng.register_rule("rule", **kwargs)
        assert exc.value.code == "ACHIEVEMENT_INVALID_RULE"

    rule = add_rule(eng)
    assert (rule.rule_id, rule.version) == ("slayer", 1)
    with pytest.raises(ar.AchievementError) as exc:
        add_rule(eng)
    assert exc.value.code == "ACHIEVEMENT_RULE_EXISTS"


def test_record_event_validates_input():
    eng = engine()
    for kwargs in [
        dict(player="", metric="kills", amount=1, occurred_at=T1),
        dict(player="p1", metric="", amount=1, occurred_at=T1),
        dict(player="p1", metric="kills", amount=0, occurred_at=T1),
        dict(player="p1", metric="kills", amount=-2, occurred_at=T1),
    ]:
        with pytest.raises(ar.AchievementError) as exc:
            eng.record_event(**kwargs)
        assert exc.value.code == "ACHIEVEMENT_INVALID_EVENT"
    assert eng.stats()["events"] == 0


def test_progress_sums_metric_amounts():
    eng = engine()
    add_rule(eng)
    eng.record_event(player="p1", metric="kills", amount=2, occurred_at=T1)
    eng.record_event(player="p1", metric="kills", amount=3, occurred_at=T2)
    assert eng.progress(player="p1", rule_id="slayer") == 5


def test_progress_ignores_other_metrics_and_players():
    eng = engine()
    add_rule(eng)
    eng.record_event(player="p1", metric="kills", amount=2, occurred_at=T1)
    eng.record_event(player="p1", metric="deaths", amount=9, occurred_at=T1)
    eng.record_event(player="p2", metric="kills", amount=7, occurred_at=T1)
    assert eng.progress(player="p1", rule_id="slayer") == 2


def test_evaluate_returns_sorted_achieved_rule_ids():
    eng = engine()
    add_rule(eng, rule_id="zeta", threshold=1)
    add_rule(eng, rule_id="alpha", threshold=1)
    eng.record_event(player="p1", metric="kills", amount=1, occurred_at=T1)
    assert eng.evaluate(player="p1") == ["alpha", "zeta"]


def test_evaluate_returns_empty_when_threshold_not_met():
    eng = engine()
    add_rule(eng)
    eng.record_event(player="p1", metric="kills", amount=4, occurred_at=T1)
    assert eng.evaluate(player="p1") == []


def test_grant_is_idempotent_per_rule_version():
    eng = engine()
    add_rule(eng)
    eng.record_event(player="p1", metric="kills", amount=5, occurred_at=T1)
    first = eng.grant(player="p1", rule_ids=["slayer"])
    second = eng.grant(player="p1", rule_ids=["slayer"])
    assert len(first) == 1
    assert second == []
    assert eng.stats()["grants"] == 1


def test_grant_record_carries_reward_and_version():
    eng = engine()
    add_rule(eng)
    eng.record_event(player="p1", metric="kills", amount=5, occurred_at=T1)
    record = eng.grant(player="p1", rule_ids=["slayer"])[0]
    assert (record.player, record.rule_id, record.version, record.reward) == (
        "p1", "slayer", 1, "TITLE-SLAYER")


def test_recompute_grants_historically_met_rules():
    eng = engine()
    add_rule(eng, threshold=3)
    eng.record_event(player="p1", metric="kills", amount=3, occurred_at=T1)
    eng.record_event(player="p2", metric="kills", amount=2, occurred_at=T1)
    report = eng.recompute(players=["p1", "p2"])
    assert (report.processed, report.granted, report.remaining,
            report.batches) == (2, 1, 0, 1)
    assert "slayer" in eng.achievements_of("p1")
    assert eng.achievements_of("p2") == {}


def test_recompute_does_not_process_players_twice():
    eng = engine()
    add_rule(eng, threshold=1)
    eng.record_event(player="p1", metric="kills", amount=1, occurred_at=T1)
    eng.recompute(players=["p1"])
    report = eng.recompute(players=["p1"])
    assert (report.processed, report.granted, report.remaining,
            report.batches) == (0, 0, 0, 0)
    assert eng.stats()["grants"] == 1


def test_recompute_splits_players_into_batches():
    eng = engine(batch_size=2)
    add_rule(eng, threshold=1)
    players = [f"p{index}" for index in range(1, 6)]
    for player in players:
        eng.record_event(player=player, metric="kills", amount=1,
                         occurred_at=T1)
    report = eng.recompute(players=players)
    assert (report.processed, report.granted, report.remaining,
            report.batches) == (5, 5, 0, 3)


def test_pending_players_clears_after_recompute():
    eng = engine()
    add_rule(eng, threshold=1)
    for player in ("p3", "p1", "p2"):
        eng.record_event(player=player, metric="kills", amount=1,
                         occurred_at=T1)
    assert eng.pending_players() == ["p1", "p2", "p3"]
    eng.recompute(players=["p1", "p2", "p3"])
    assert eng.pending_players() == []


def test_progress_for_unknown_rule_is_rejected():
    eng = engine()
    with pytest.raises(ar.AchievementError) as exc:
        eng.progress(player="p1", rule_id="missing")
    assert exc.value.code == "ACHIEVEMENT_UNKNOWN_RULE"


def test_snapshot_is_detached_and_restore_roundtrips():
    eng = engine()
    add_rule(eng, threshold=1)
    eng.record_event(player="p1", metric="kills", amount=1, occurred_at=T1)
    eng.recompute(players=["p1"])
    state = eng.snapshot()
    assert set(state) == {"version", "rules", "events", "grants", "processed",
                          "stats", "next_seq"}
    assert state["version"] == 1
    pristine = copy.deepcopy(state)

    state["grants"].clear()
    state["events"].clear()
    assert eng.achievements_of("p1")["slayer"]["status"] == "granted"

    clone = engine()
    clone.restore(pristine)
    info = clone.achievements_of("p1")
    assert info["slayer"]["reward"] == "TITLE-SLAYER"
    assert info["slayer"]["version"] == 1
    assert clone.recompute(players=["p1"]).granted == 0


def test_restore_rejects_malformed_state_without_touching_data():
    eng = engine()
    add_rule(eng, threshold=1)
    eng.record_event(player="p1", metric="kills", amount=1, occurred_at=T1)
    eng.recompute(players=["p1"])
    good = eng.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, events=[]),
        {key: value for key, value in good.items() if key != "rules"},
    ]
    for bad in bad_states:
        with pytest.raises(ar.AchievementError) as exc:
            eng.restore(bad)
        assert exc.value.code == "ACHIEVEMENT_SNAPSHOT_INVALID"
    assert "slayer" in eng.achievements_of("p1")
    assert eng.stats()["events"] == 1
