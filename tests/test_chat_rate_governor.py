import pytest

import chat_rate_governor as cg


def governor(**kwargs):
    return cg.ChatRateGovernor(**kwargs)


def test_allow_consumes_tokens():
    g = governor(capacity=3, refill_per_second=0.0)
    for _ in range(3):
        out = g.allow("p1", "world", at=0.0)
        assert out["allowed"] is True
        assert out["reason"] == "ok"
    denied = g.allow("p1", "world", at=0.0)
    assert denied["allowed"] is False
    assert denied["reason"] == "rate_limited"
    assert denied["tokens"] == 0.0


def test_tokens_refill_over_time():
    g = governor(capacity=3, refill_per_second=1.0)
    for _ in range(3):
        g.allow("p1", "world", at=0.0)
    out = g.allow("p1", "world", at=2.0)
    assert out["allowed"] is True
    assert out["tokens"] == 1.0


def test_tokens_are_capped_at_capacity():
    g = governor(capacity=4, refill_per_second=1.0)
    for _ in range(4):
        g.allow("p1", "world", at=0.0)
    out = g.allow("p1", "world", at=100.0)
    assert out["allowed"] is True
    assert out["tokens"] == 3.0
    assert g.state("p1", "world")["tokens"] == 3.0


def test_channels_have_independent_buckets():
    g = governor(capacity=1, refill_per_second=0.0)
    assert g.allow("p1", "world", at=0.0)["allowed"] is True
    assert g.allow("p1", "world", at=0.0)["allowed"] is False
    assert g.allow("p1", "guild", at=0.0)["allowed"] is True


def test_players_have_independent_buckets():
    g = governor(capacity=1, refill_per_second=0.0)
    assert g.allow("p1", "world", at=0.0)["allowed"] is True
    assert g.allow("p2", "world", at=0.0)["allowed"] is True
    assert g.allow("p1", "world", at=0.0)["allowed"] is False


def test_muted_player_is_rejected_without_spending_tokens():
    g = governor(capacity=5, refill_per_second=0.0)
    g.mute("p1", until=100, reason="spam")
    out = g.allow("p1", "world", at=10)
    assert out["allowed"] is False
    assert out["reason"] == "muted"
    assert g.state("p1", "world")["tokens"] == 5.0


def test_mute_lifts_after_until():
    g = governor(capacity=2, refill_per_second=0.0)
    g.mute("p1", until=100)
    assert g.is_muted("p1", at=50) is True
    assert g.is_muted("p1", at=150) is False
    out = g.allow("p1", "world", at=150)
    assert out["allowed"] is True


def test_unmute_reports_previous_state():
    g = governor()
    assert g.unmute("p1") is False
    g.mute("p1", until=100)
    assert g.unmute("p1") is True
    assert g.is_muted("p1", at=50) is False


def test_mute_rejects_non_positive_until():
    g = governor()
    with pytest.raises(cg.GovernorError) as ei:
        g.mute("p1", until=0)
    assert ei.value.code == "GOV_BAD_UNTIL"
    with pytest.raises(cg.GovernorError) as ei2:
        g.mute("p1", until=-5)
    assert ei2.value.code == "GOV_BAD_UNTIL"


def test_record_folds_repeated_content():
    g = governor(fold_window_seconds=30)
    first = g.record("p1", "world", "spam", at=0.0)
    assert first["folded"] is False
    assert first["count"] == 1
    second = g.record("p1", "world", "spam", at=5.0)
    assert second["folded"] is True
    assert second["count"] == 2
    third = g.record("p1", "world", "spam", at=20.0)
    assert third["count"] == 3
    assert g.fold_count("world", "p1") == 3


def test_record_outside_window_starts_new_group():
    g = governor(fold_window_seconds=30)
    g.record("p1", "world", "spam", at=0.0)
    out = g.record("p1", "world", "spam", at=100.0)
    assert out["folded"] is False
    assert out["count"] == 1
    assert g.fold_count("world", "p1") == 1


def test_fold_count_without_record_is_zero():
    g = governor()
    assert g.fold_count("world", "p1") == 0
    g.record("p1", "world", "x", at=0.0)
    assert g.fold_count("world", "p2") == 0
    assert g.fold_count("guild", "p1") == 0


def test_state_reports_tokens_capacity_and_fold():
    g = governor(capacity=5, refill_per_second=1.0)
    g.allow("p1", "world", at=0.0)
    g.record("p1", "world", "a", at=0.0)
    g.record("p1", "world", "a", at=1.0)
    st = g.state("p1", "world")
    assert st["tokens"] == 4.0
    assert st["capacity"] == 5.0
    assert st["fold"] == 2


def test_capacity_and_rate_validation():
    g = governor()
    with pytest.raises(cg.GovernorError) as ei:
        g.set_capacity(0)
    assert ei.value.code == "GOV_BAD_CAPACITY"
    with pytest.raises(cg.GovernorError) as ei2:
        g.set_capacity(101)
    assert ei2.value.code == "GOV_BAD_CAPACITY"
    g.set_capacity(10)
    assert g.state("fresh", "world")["capacity"] == 10.0
    with pytest.raises(cg.GovernorError) as ei3:
        g.set_refill_rate(-1)
    assert ei3.value.code == "GOV_BAD_RATE"
    g.set_refill_rate(0)
    assert g.state("fresh", "world")["tokens"] == 10.0


def test_snapshot_restore_roundtrip():
    g = governor(capacity=5, refill_per_second=0.0)
    g.mute("p1", until=100)
    g.allow("p2", "world", at=0.0)
    g.record("p1", "world", "spam", at=0.0)
    g.record("p1", "world", "spam", at=1.0)
    snap = g.snapshot()
    g.allow("p2", "world", at=2.0)
    assert g.state("p2", "world")["tokens"] == 3.0
    g.restore(snap)
    assert g.state("p2", "world")["tokens"] == 4.0
    assert g.is_muted("p1", at=50) is True
    assert g.fold_count("world", "p1") == 2
    assert g.snapshot() == snap
    with pytest.raises(cg.GovernorError) as ei:
        g.restore({"buckets": []})
    assert ei.value.code == "GOV_SNAPSHOT_INVALID"
    assert g.state("p2", "world")["tokens"] == 4.0
