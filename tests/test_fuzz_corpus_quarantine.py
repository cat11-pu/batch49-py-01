import copy
import threading

import pytest

import fuzz_corpus_quarantine as fq

SHA = "a" * 64
T0 = "2026-11-24T08:00:00Z"
T1 = "2026-11-24T08:05:00Z"
T2 = "2026-11-24T08:10:00Z"
T3 = "2026-11-24T08:15:00Z"
T4 = "2026-11-24T08:20:00Z"
T5 = "2026-11-24T08:25:00Z"


def app(**kwargs):
    return fq.FuzzCorpusQuarantine(**kwargs)


def submit(store, corpus_id="c-1", fingerprint="FP-0001", *, severity=3, at=T0):
    return store.submit(corpus_id, fingerprint, severity=severity,
                        payload_sha=SHA, at=at)


def test_submit_returns_quarantined_entry_with_metadata():
    store = app()
    entry = submit(store, "c-1", "FP-AAAA0001", severity=4, at=T0)
    assert entry.state == "quarantined"
    assert entry.corpus_id == "c-1"
    assert entry.fingerprint == "FP-AAAA0001"
    assert entry.severity == 4
    assert entry.payload_sha == SHA
    assert entry.reproductions == 0
    assert entry.streak == 0
    assert entry.last_reproduced_at is None
    assert entry.submitted_at == T0
    assert [row.corpus_id for row in store.entries()] == ["c-1"]
    assert store.stats() == {"submitted": 1, "reproductions": 0, "unstable": 0,
                             "evicted": 0, "promoted": 0}


def test_submit_rejects_blank_identifiers_and_bad_severity():
    store = app()
    bad_calls = [
        dict(corpus_id="", fingerprint="FP-1", severity=3),
        dict(corpus_id="c-1", fingerprint="   ", severity=3),
        dict(corpus_id="c-1", fingerprint="FP-1", severity=0),
        dict(corpus_id="c-1", fingerprint="FP-1", severity=6),
    ]
    for kwargs in bad_calls:
        with pytest.raises(fq.QuarantineError) as exc:
            store.submit(kwargs["corpus_id"], kwargs["fingerprint"],
                         severity=kwargs["severity"], payload_sha=SHA, at=T0)
        assert exc.value.code == "QUARANTINE_INVALID"
    assert store.entries() == []
    assert store.stats()["submitted"] == 0


def test_entries_order_by_severity_then_time_then_id():
    store = app()
    submit(store, "c-2", "FP-2", severity=5, at=T1)
    submit(store, "c-1", "FP-1", severity=3, at=T2)
    submit(store, "c-3", "FP-3", severity=5, at=T0)
    assert [row.corpus_id for row in store.entries()] == ["c-3", "c-2", "c-1"]


def test_record_reproduction_increments_and_tracks_time():
    store = app()
    submit(store)
    first = store.record_reproduction("c-1", reproduced=True, at=T1)
    second = store.record_reproduction("c-1", reproduced=True, at=T2)
    assert second.reproductions == 2
    assert second.streak == 2
    assert second.last_reproduced_at == T2
    assert second.state == "quarantined"
    assert first.last_reproduced_at == T1
    assert store.stats()["reproductions"] == 2


def test_non_reproducing_run_clears_streak_and_marks_unstable():
    store = app()
    submit(store)
    store.record_reproduction("c-1", reproduced=True, at=T1)
    store.record_reproduction("c-1", reproduced=True, at=T2)
    entry = store.record_reproduction("c-1", reproduced=False, at=T3)
    assert entry.reproductions == 3
    assert entry.streak == 0
    assert entry.state == "unstable"
    assert entry.last_reproduced_at == T3
    assert store.stats()["unstable"] == 1


def test_mark_unstable_is_repeatable_but_counted_once():
    store = app()
    submit(store)
    first = store.mark_unstable("c-1", at=T1)
    second = store.mark_unstable("c-1", at=T2)
    assert first.state == "unstable"
    assert second.state == "unstable"
    assert second.last_reproduced_at is None
    assert store.stats()["unstable"] == 1


def test_promote_after_required_reproductions_creates_issue():
    store = app(required_reproductions=3)
    submit(store, "c-1", "FP-DEAD00AA")
    for moment in (T1, T2, T3):
        store.record_reproduction("c-1", reproduced=True, at=moment)
    promotion = store.promote("c-1", at=T4)
    assert promotion.corpus_id == "c-1"
    assert promotion.fingerprint == "FP-DEAD00AA"
    assert promotion.issue_id.startswith("ISSUE-")
    assert promotion.promoted_at == T4
    promoted = store.entry("c-1")
    assert promoted.state == "promoted"
    assert [row.corpus_id for row in store.promotions()] == ["c-1"]
    assert store.entries(state="quarantined") == []
    assert store.stats()["promoted"] == 1


def test_promote_unknown_corpus_raises():
    store = app()
    with pytest.raises(fq.QuarantineError) as exc:
        store.promote("c-missing", at=T0)
    assert exc.value.code == "QUARANTINE_NOT_FOUND"
    assert store.stats()["promoted"] == 0


def test_promote_twice_is_refused():
    store = app(required_reproductions=1)
    submit(store)
    store.record_reproduction("c-1", reproduced=True, at=T1)
    store.promote("c-1", at=T2)
    with pytest.raises(fq.QuarantineError) as exc:
        store.promote("c-1", at=T3)
    assert exc.value.code == "QUARANTINE_STATE"
    assert len(store.promotions()) == 1


def test_prune_evicts_oldest_reproduction_first():
    store = app(capacity=2)
    submit(store, "c-1", "FP-1", at=T0)
    submit(store, "c-2", "FP-2", at=T1)
    submit(store, "c-3", "FP-3", at=T2)
    store.record_reproduction("c-1", reproduced=True, at=T3)
    store.record_reproduction("c-2", reproduced=True, at=T4)
    removed = store.prune(at=T5)
    assert [row.corpus_id for row in removed] == ["c-3"]
    assert removed[0].state == "evicted"
    assert [row.corpus_id for row in store.entries()] == ["c-1", "c-2"]
    assert [row.corpus_id for row in store.evicted()] == ["c-3"]
    assert store.stats()["evicted"] == 1


def test_prune_tie_breaks_on_lower_severity():
    store = app(capacity=1)
    submit(store, "c-1", "FP-1", severity=2, at=T0)
    submit(store, "c-2", "FP-2", severity=5, at=T0)
    store.record_reproduction("c-1", reproduced=True, at=T1)
    store.record_reproduction("c-2", reproduced=True, at=T1)
    removed = store.prune(at=T2)
    assert [row.corpus_id for row in removed] == ["c-1"]
    assert [row.corpus_id for row in store.entries()] == ["c-2"]


def test_evicted_metadata_is_retained_and_promotion_refused():
    store = app(capacity=1)
    submit(store, "c-1", "FP-1", severity=4, at=T0)
    submit(store, "c-2", "FP-2", severity=4, at=T1)
    store.prune(at=T2)
    evicted = store.entry("c-1")
    assert evicted is not None
    assert evicted.state == "evicted"
    assert evicted.fingerprint == "FP-1"
    assert evicted.severity == 4
    with pytest.raises(fq.QuarantineError) as exc:
        store.promote("c-1", at=T3)
    assert exc.value.code == "QUARANTINE_EVICTED"
    with pytest.raises(fq.QuarantineError) as exc:
        store.record_reproduction("c-1", reproduced=True, at=T3)
    assert exc.value.code == "QUARANTINE_EVICTED"


def test_resubmitting_evicted_corpus_returns_it_to_quarantine():
    store = app(capacity=1)
    submit(store, "c-1", "FP-1", severity=3, at=T0)
    submit(store, "c-2", "FP-2", severity=3, at=T1)
    store.prune(at=T2)
    assert [row.corpus_id for row in store.evicted()] == ["c-1"]
    again = store.submit("c-1", "FP-1", severity=2, payload_sha=SHA, at=T5)
    assert again.state == "quarantined"
    assert again.reproductions == 0
    assert again.streak == 0
    assert again.submitted_at == T0
    assert again.severity == 2
    assert store.evicted() == []


def test_snapshot_restore_roundtrip_and_bad_state_rejected():
    store = app(required_reproductions=1)
    submit(store, "c-1", "FP-1", severity=4, at=T0)
    store.record_reproduction("c-1", reproduced=True, at=T1)
    store.promote("c-1", at=T2)
    state = store.snapshot()
    assert set(state) == {"version", "entries", "promotions", "counters", "stats"}
    pristine = copy.deepcopy(state)
    state["entries"].clear()
    assert store.entry("c-1").state == "promoted"

    clone = app()
    clone.restore(pristine)
    assert clone.entry("c-1").state == "promoted"
    assert len(clone.promotions()) == 1
    assert clone.stats() == store.stats()

    bad_states = [None, {}, {"version": 1, "entries": "nope", "promotions": [],
                            "counters": {}, "stats": {}}]
    for bad in bad_states:
        with pytest.raises(fq.QuarantineError) as exc:
            clone.restore(bad)
        assert exc.value.code == "QUARANTINE_SNAPSHOT_INVALID"
    assert clone.entry("c-1").state == "promoted"


def test_concurrent_reproduction_and_promotion_are_serialized():
    store = app(required_reproductions=8)
    submit(store, "c-1", "FP-9", severity=5, at=T0)
    failures = []
    barrier = threading.Barrier(8)

    def reproduce():
        try:
            barrier.wait(timeout=5)
            store.record_reproduction("c-1", reproduced=True, at=T1)
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(repr(exc))

    threads = [threading.Thread(target=reproduce) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert failures == []
    entry = store.entry("c-1")
    assert entry.reproductions == 8
    assert entry.streak == 8

    outcomes = []
    guard = threading.Lock()
    gate = threading.Barrier(2)

    def promote():
        try:
            gate.wait(timeout=5)
            store.promote("c-1", at=T2)
            with guard:
                outcomes.append("ok")
        except fq.QuarantineError as exc:
            with guard:
                outcomes.append(exc.code)

    racers = [threading.Thread(target=promote) for _ in range(2)]
    for thread in racers:
        thread.start()
    for thread in racers:
        thread.join()
    assert sorted(outcomes) == ["QUARANTINE_STATE", "ok"]
    assert len(store.promotions()) == 1
