import pytest

import chat_recall_journal as cj

T0 = "2026-11-25T10:00:00"
T1 = "2026-11-25T10:00:05"
T2 = "2026-11-25T10:00:10"
T3 = "2026-11-25T10:00:15"


def journal(**kwargs):
    return cj.ChatRecallJournal(**kwargs)


def test_post_assigns_monotonic_seq():
    j = journal()
    m1 = j.post("world", "alice", "hello", at=T0)
    m2 = j.post("world", "bob", "hi", at=T1)
    assert (m1.seq, m2.seq) == (1, 2)
    assert m1.version == 1
    assert m1.edited_at is None
    assert m1.posted_at == T0
    assert j.seq_of("world") == 2
    assert j.channels() == ["world"]


def test_post_rejects_empty_fields():
    j = journal()
    with pytest.raises(cj.ChatError) as ei:
        j.post("", "alice", "x", at=T0)
    assert ei.value.code == "CHAT_INVALID"
    with pytest.raises(cj.ChatError) as ei2:
        j.post("world", "", "x", at=T0)
    assert ei2.value.code == "CHAT_INVALID"
    with pytest.raises(cj.ChatError) as ei3:
        j.post("world", "alice", "", at=T0)
    assert ei3.value.code == "CHAT_INVALID"


def test_post_rejects_oversized_body_and_bad_limit():
    j = journal(body_limit=500)
    with pytest.raises(cj.ChatError) as ei:
        j.post("world", "alice", "x" * 501, at=T0)
    assert ei.value.code == "CHAT_BODY_TOO_LONG"
    assert j.post("world", "alice", "x" * 500, at=T0).seq == 1
    with pytest.raises(cj.ChatError) as ei2:
        j.set_version_limit(0)
    assert ei2.value.code == "CHAT_INVALID"


def test_seq_of_unknown_channel_is_zero():
    j = journal()
    assert j.seq_of("never") == 0
    assert j.channels() == []


def test_edit_bumps_version():
    j = journal()
    j.post("world", "alice", "first", at=T0)
    edited = j.edit("world", 1, "alice", "fixed", expected_version=1, at=T1)
    assert edited.version == 2
    assert edited.body == "fixed"
    assert edited.edited_at == T1
    assert edited.seq == 1
    assert j.latest("world", 1)["body"] == "fixed"
    assert j.latest("world", 1)["version"] == 2


def test_edit_requires_matching_expected_version():
    j = journal()
    j.post("world", "alice", "first", at=T0)
    j.edit("world", 1, "alice", "second", expected_version=1, at=T1)
    with pytest.raises(cj.ChatError) as ei:
        j.edit("world", 1, "alice", "third", expected_version=1, at=T2)
    assert ei.value.code == "CHAT_VERSION_CONFLICT"
    assert j.latest("world", 1)["body"] == "second"
    assert j.latest("world", 1)["version"] == 2


def test_edit_forbidden_for_other_author():
    j = journal()
    j.post("world", "alice", "first", at=T0)
    with pytest.raises(cj.ChatError) as ei:
        j.edit("world", 1, "bob", "hijack", expected_version=1, at=T1)
    assert ei.value.code == "CHAT_FORBIDDEN"
    assert j.latest("world", 1)["body"] == "first"


def test_edit_on_unknown_seq_raises():
    j = journal()
    j.post("world", "alice", "first", at=T0)
    with pytest.raises(cj.ChatError) as ei:
        j.edit("world", 9, "alice", "x", expected_version=1, at=T1)
    assert ei.value.code == "CHAT_NOT_FOUND"


def test_recall_returns_tombstone():
    j = journal()
    j.post("world", "alice", "bad words", at=T0)
    tomb = j.recall("world", 1, "alice", at="2026-11-25T10:01:00")
    assert isinstance(tomb, cj.Tombstone)
    assert tomb.seq == 1
    assert tomb.channel == "world"
    assert tomb.author == "alice"
    assert tomb.recalled_by == "alice"
    assert tomb.recalled_at == "2026-11-25T10:01:00"


def test_recall_twice_is_rejected():
    j = journal()
    j.post("world", "alice", "bad words", at=T0)
    j.recall("world", 1, "alice", at="2026-11-25T10:00:30")
    with pytest.raises(cj.ChatError) as ei:
        j.recall("world", 1, "alice", at="2026-11-25T10:00:40")
    assert ei.value.code == "CHAT_RECALLED"


def test_recall_outside_window_is_rejected():
    j = journal(recall_window_seconds=120)
    j.post("world", "alice", "bad words", at=T0)
    with pytest.raises(cj.ChatError) as ei:
        j.recall("world", 1, "alice", at="2026-11-25T10:05:00")
    assert ei.value.code == "CHAT_RECALL_WINDOW"


def test_recall_forbidden_for_other_author():
    j = journal()
    j.post("world", "alice", "bad words", at=T0)
    with pytest.raises(cj.ChatError) as ei:
        j.recall("world", 1, "bob", at="2026-11-25T10:00:30")
    assert ei.value.code == "CHAT_FORBIDDEN"


def test_fetch_range_orders_and_validates():
    j = journal()
    for body in ("a", "b", "c", "d"):
        j.post("world", "alice", body, at=T0)
    rows = j.fetch_range("world", 2, 3)
    assert [r["seq"] for r in rows] == [2, 3]
    assert [r["kind"] for r in rows] == ["message", "message"]
    assert [r["body"] for r in rows] == ["b", "c"]
    with pytest.raises(cj.ChatError) as ei:
        j.fetch_range("world", 0, 2)
    assert ei.value.code == "CHAT_INVALID"
    with pytest.raises(cj.ChatError) as ei2:
        j.fetch_range("world", 3, 2)
    assert ei2.value.code == "CHAT_INVALID"


def test_history_is_admin_only_and_ordered():
    j = journal()
    j.post("world", "alice", "v1", at=T0)
    j.edit("world", 1, "alice", "v2", expected_version=1, at=T1)
    j.edit("world", 1, "alice", "v3", expected_version=2, at=T2)
    with pytest.raises(cj.ChatError) as ei:
        j.history("world", 1)
    assert ei.value.code == "CHAT_FORBIDDEN"
    versions = j.history("world", 1, role="admin")
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert [v["body"] for v in versions] == ["v1", "v2", "v3"]


def test_snapshot_restore_roundtrip():
    j = journal()
    j.post("world", "alice", "first", at=T0)
    j.post("world", "bob", "second", at=T1)
    j.edit("world", 1, "alice", "fixed", expected_version=1, at=T2)
    j.recall("world", 2, "bob", at=T3)
    snap = j.snapshot()
    j.edit("world", 1, "alice", "again", expected_version=2, at=T3)
    assert j.latest("world", 1)["version"] == 3
    j.restore(snap)
    assert j.seq_of("world") == 2
    assert j.latest("world", 1)["body"] == "fixed"
    assert j.latest("world", 1)["version"] == 2
    assert j.snapshot() == snap
    with pytest.raises(cj.ChatError) as ei:
        j.restore({"channels": []})
    assert ei.value.code == "CHAT_SNAPSHOT_INVALID"
    assert j.seq_of("world") == 2
