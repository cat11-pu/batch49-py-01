import pytest

import guild_activity_review as gr


def review(**kwargs):
    return gr.GuildActivityReview(**kwargs)


def test_join_member_sets_active_status():
    r = review()
    out = r.join_member("m1", 2, week="2026-W47")
    assert out["member"] == "m1"
    assert out["rank"] == 2
    assert out["week"] == "2026-W47"
    assert r.status("m1") == "active"
    assert r.rank_of("m1") == 2


def test_join_member_rejects_duplicate_and_bad_rank():
    r = review()
    r.join_member("m1", 2, week="2026-W47")
    with pytest.raises(gr.ReviewError) as ei:
        r.join_member("m1", 3, week="2026-W47")
    assert ei.value.code == "REVIEW_DUPLICATE"
    with pytest.raises(gr.ReviewError) as ei2:
        r.join_member("m2", 0, week="2026-W47")
    assert ei2.value.code == "REVIEW_BAD_RANK"


def test_week_of_splits_by_local_timezone():
    r = review()
    assert r.week_of("2026-11-18T12:00:00") == "2026-W47"
    assert r.week_of("2026-11-23T09:00:00") == "2026-W48"
    assert r.week_of("2026-11-15T12:00:00") == "2026-W46"
    assert r.week_of("2026-11-16T00:30:00") == "2026-W47"


def test_contribution_accumulates_within_week():
    r = review()
    r.join_member("m1", 2, week="2026-W47")
    first = r.record_contribution("m1", 40, occurred_at="2026-11-18T10:00:00")
    assert first["total"] == 40
    assert first["week"] == "2026-W47"
    second = r.record_contribution("m1", 70, occurred_at="2026-11-19T10:00:00")
    assert second["total"] == 110


def test_contribution_rejects_bad_amount_and_unknown_member():
    r = review()
    r.join_member("m1", 2, week="2026-W47")
    with pytest.raises(gr.ReviewError) as ei:
        r.record_contribution("m1", 0, occurred_at="2026-11-18T10:00:00")
    assert ei.value.code == "REVIEW_BAD_AMOUNT"
    with pytest.raises(gr.ReviewError) as ei2:
        r.record_contribution("m1", -5, occurred_at="2026-11-18T10:00:00")
    assert ei2.value.code == "REVIEW_BAD_AMOUNT"
    with pytest.raises(gr.ReviewError) as ei3:
        r.record_contribution("ghost", 10, occurred_at="2026-11-18T10:00:00")
    assert ei3.value.code == "REVIEW_NO_MEMBER"


def test_miss_below_threshold_enters_probation():
    r = review(weekly_threshold=100)
    r.join_member("m1", 2, week="2026-W47")
    r.record_contribution("m1", 60, occurred_at="2026-11-18T10:00:00")
    out = r.weekly_review("2026-W47")
    assert out["week"] == "2026-W47"
    assert out["probation"] == ["m1"]
    assert out["demoted"] == []
    assert out["removed"] == []
    assert r.status("m1") == "probation"
    assert r.observe("m1")["missed"] == 1


def test_second_miss_demotes_one_rank():
    r = review(weekly_threshold=100)
    r.join_member("m1", 2, week="2026-W47")
    r.weekly_review("2026-W47")
    out = r.weekly_review("2026-W48")
    assert out["demoted"] == ["m1"]
    assert r.rank_of("m1") == 1
    assert r.observe("m1")["missed"] == 2


def test_meeting_threshold_clears_probation():
    r = review(weekly_threshold=100)
    r.join_member("m1", 3, week="2026-W47")
    r.record_contribution("m1", 150, occurred_at="2026-11-18T10:00:00")
    out = r.weekly_review("2026-W47")
    assert out["probation"] == []
    assert r.status("m1") == "active"
    r.weekly_review("2026-W48")
    assert r.status("m1") == "probation"
    r.record_contribution("m1", 200, occurred_at="2026-12-01T10:00:00")
    r.weekly_review("2026-W49")
    assert r.status("m1") == "active"
    assert r.observe("m1")["missed"] == 0


def test_repeated_review_of_same_week_is_idempotent():
    r = review(weekly_threshold=100)
    r.join_member("m1", 2, week="2026-W47")
    r.weekly_review("2026-W47")
    first = r.weekly_review("2026-W48")
    assert first["demoted"] == ["m1"]
    second = r.weekly_review("2026-W48")
    assert second == first
    assert r.rank_of("m1") == 1


def test_lowest_rank_is_removed_after_second_miss():
    r = review(weekly_threshold=100)
    r.join_member("m1", 1, week="2026-W47")
    r.weekly_review("2026-W47")
    out = r.weekly_review("2026-W48")
    assert out["removed"] == ["m1"]
    assert r.status("m1") == "removed"
    assert r.rank_of("m1") == 0


def test_appeal_outside_grace_window_is_rejected():
    r = review(weekly_threshold=100)
    r.join_member("m1", 2, week="2026-W47")
    r.weekly_review("2026-W47")
    r.weekly_review("2026-W48")
    with pytest.raises(gr.ReviewError) as ei:
        r.appeal("m1", at="2026-12-15T10:00:00")
    assert ei.value.code == "REVIEW_APPEAL_WINDOW"
    assert r.status("m1") == "probation"


def test_appeal_and_restore_rank():
    r = review(weekly_threshold=100)
    r.join_member("m1", 3, week="2026-W47")
    r.weekly_review("2026-W47")
    r.weekly_review("2026-W48")
    assert r.rank_of("m1") == 2
    out = r.appeal("m1", at="2026-11-25T10:00:00")
    assert out["accepted"] is True
    assert out["previous_rank"] == 3
    assert r.status("m1") == "appealed"
    res = r.restore_rank("m1")
    assert res["member"] == "m1"
    assert res["rank"] == 3
    assert r.rank_of("m1") == 3
    assert r.status("m1") == "active"


def test_restore_rank_requires_appeal():
    r = review()
    r.join_member("m1", 2, week="2026-W47")
    with pytest.raises(gr.ReviewError) as ei:
        r.restore_rank("m1")
    assert ei.value.code == "REVIEW_NOT_APPEALED"


def test_set_threshold_validates_and_applies_later():
    r = review(weekly_threshold=100)
    with pytest.raises(gr.ReviewError) as ei:
        r.set_threshold(-1)
    assert ei.value.code == "REVIEW_BAD_AMOUNT"
    r.join_member("m1", 2, week="2026-W47")
    r.record_contribution("m1", 50, occurred_at="2026-11-18T10:00:00")
    r.set_threshold(40)
    out = r.weekly_review("2026-W47")
    assert out["probation"] == []
    assert r.status("m1") == "active"


def test_snapshot_restore_roundtrip():
    r = review(weekly_threshold=100)
    r.join_member("m1", 2, week="2026-W47")
    r.weekly_review("2026-W47")
    snap = r.snapshot()
    r.weekly_review("2026-W48")
    assert r.rank_of("m1") == 1
    r.restore(snap)
    assert r.rank_of("m1") == 2
    assert r.status("m1") == "probation"
    assert r.snapshot() == snap
    with pytest.raises(gr.ReviewError) as ei:
        r.restore({"members": []})
    assert ei.value.code == "REVIEW_SNAPSHOT_INVALID"
    assert r.rank_of("m1") == 2
