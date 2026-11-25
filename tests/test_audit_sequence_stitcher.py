import pytest

import audit_sequence_stitcher as ass


def stitcher(**kwargs):
    return ass.AuditSequenceStitcher(**kwargs)


def test_ingest_contiguous_and_event_view():
    s = stitcher()
    assert s.ingest("a", 1, {"m": 1}, ts=1)["status"] == "accepted"
    assert s.ingest("a", 2, {"m": 2}, ts=2)["status"] == "accepted"
    out = s.ingest("a", 3, {"m": 3}, ts=3)
    assert out == {"status": "accepted", "frontier": 3}
    assert s.sources() == ["a"]
    assert s.gaps("a") == []
    assert [row["seq"] for row in s.events("a")] == [1, 2, 3]
    assert s.events("a")[1] == {"seq": 2, "body": {"m": 2}, "ts": 2}
    assert s.stats()["accepted"] == 3


def test_gap_interval_reported_with_discovery_time():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 6, "six", ts=5)
    gap = s.gaps("a")[0]
    assert (gap.source, gap.start, gap.end, gap.missing) == ("a", 2, 5, 4)
    assert gap.discovered_at == 5
    assert gap.status == "open"


def test_out_of_order_buffered_then_drained():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    assert s.ingest("a", 3, "three", ts=3)["status"] == "buffered"
    assert s.stats()["buffered"] == 1
    assert [row["seq"] for row in s.events("a")] == [1]
    out = s.ingest("a", 2, "two", ts=2)
    assert out["frontier"] == 3
    assert [row["seq"] for row in s.events("a")] == [1, 2, 3]
    assert s.gaps("a") == []


def test_duplicate_seq_reported():
    s = stitcher()
    s.ingest("a", 2, "first", ts=1)
    out = s.ingest("a", 2, "first", ts=2)
    assert out["status"] == "duplicate"
    assert out["frontier"] == 0
    assert len(s.events("a")) == 0
    assert s.stats()["duplicates"] == 1
    assert s.stats()["accepted"] == 1


def test_view_sorted_by_seq():
    s = stitcher()
    s.ingest("a", 3, "three", ts=1)
    s.ingest("a", 1, "one", ts=2)
    s.ingest("a", 2, "two", ts=3)
    assert [row["seq"] for row in s.events("a")] == [1, 2, 3]
    assert [row["body"] for row in s.events("a")] == ["one", "two", "three"]


def test_backfill_shrinks_then_clears_gap():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 4, "four", ts=2)
    s.backfill("a", 2, "two", ts=3)
    gaps = s.gaps("a")
    assert [(gap.start, gap.end, gap.missing) for gap in gaps] == [(3, 3, 1)]
    s.backfill("a", 3, "three", ts=4)
    assert s.gaps("a") == []
    assert [row["seq"] for row in s.events("a")] == [1, 2, 3, 4]
    assert s.stats()["backfills"] == 2


def test_backfill_requires_known_gap():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    with pytest.raises(ass.AuditSeqError) as exc:
        s.backfill("a", 5, "future", ts=2)
    assert exc.value.code == "AUDIT_SEQ_NOT_MISSING"
    with pytest.raises(ass.AuditSeqError) as exc2:
        s.backfill("a", 1, "again", ts=3)
    assert exc2.value.code == "AUDIT_SEQ_NOT_MISSING"


def test_closed_gap_refuses_backfill():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 6, "six", ts=2)
    assert s.close_gap("a", 2) == 4
    gap = s.gaps("a")[0]
    assert gap.status == "unclosable"
    with pytest.raises(ass.AuditSeqError) as exc:
        s.backfill("a", 2, "late", ts=3)
    assert exc.value.code == "AUDIT_SEQ_GAP_CLOSED"


def test_close_gap_returns_count_and_is_idempotent():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 5, "five", ts=2)
    assert s.close_gap("a", 99) == 0
    assert s.close_gap("a", 2) == 3
    assert s.close_gap("a", 2) == 0
    assert s.stats()["gaps_closed"] == 1


def test_ack_cursor_forward_only():
    s = stitcher()
    for seq in (1, 2, 3):
        s.ingest("a", seq, seq, ts=seq)
    assert s.cursor("a") == 0
    assert s.ack("a", 1) == 1
    assert s.ack("a", 3) == 3
    assert s.cursor("a") == 3
    with pytest.raises(ass.AuditSeqError) as exc:
        s.ack("a", 2)
    assert exc.value.code == "AUDIT_SEQ_CURSOR_REGRESSION"
    assert s.cursor("a") == 3
    with pytest.raises(ass.AuditSeqError) as exc2:
        s.ack("a", 4)
    assert exc2.value.code == "AUDIT_SEQ_NOT_DELIVERED"


def test_resume_returns_next_seq():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 2, "two", ts=2)
    assert s.resume("a") == 1
    s.ack("a", 2)
    assert s.resume("a") == 3


def test_retired_source_is_frozen():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 4, "four", ts=2)
    s.ack("a", 1)
    assert s.retire("a") is True
    assert s.retire("a") is False
    assert s.cursor("a") == 1
    assert [gap.start for gap in s.gaps("a")] == [2]
    assert s.stats()["retirements"] == 1
    with pytest.raises(ass.AuditSeqError) as exc:
        s.ingest("a", 2, "two", ts=3)
    assert exc.value.code == "AUDIT_SEQ_SOURCE_RETIRED"
    with pytest.raises(ass.AuditSeqError) as exc2:
        s.ack("a", 2)
    assert exc2.value.code == "AUDIT_SEQ_SOURCE_RETIRED"


def test_invalid_ingest_input_rejected():
    s = stitcher()
    bad = [("", 1), ("a", -1), ("a", "2"), ("a", 1.5)]
    for source, seq in bad:
        with pytest.raises(ass.AuditSeqError) as exc:
            s.ingest(source, seq, "body", ts=0)
        assert exc.value.code == "AUDIT_SEQ_INVALID"
    assert s.stats()["accepted"] == 0


def test_buffer_cap_drops_oldest():
    s = stitcher(buffer_cap=2)
    s.ingest("a", 10, "ten", ts=1)
    s.ingest("a", 11, "eleven", ts=2)
    s.ingest("a", 12, "twelve", ts=3)
    assert s.stats()["dropped"] == 1
    assert s.stats()["buffered"] == 3
    gap = s.gaps("a")[0]
    assert (gap.start, gap.end, gap.missing) == (1, 10, 10)
    assert [row["seq"] for row in s.events("a")] == []


def test_snapshot_restore_roundtrip():
    s = stitcher()
    s.ingest("a", 1, "one", ts=1)
    s.ingest("a", 3, "three", ts=2)
    state = s.snapshot()
    assert state["version"] == 1
    assert "sources" in state and "stats" in state

    state["stats"]["accepted"] = 999
    assert s.stats()["accepted"] == 2

    clone = stitcher()
    clone.restore(s.snapshot())
    assert [(gap.start, gap.end) for gap in clone.gaps("a")] == [(2, 2)]
    assert clone.ingest("a", 3, "three", ts=5)["status"] == "duplicate"
    clone.backfill("a", 2, "two", ts=6)
    assert [row["seq"] for row in clone.events("a")] == [1, 2, 3]
    assert clone.stats()["accepted"] == 2
