import threading

import pytest

import suppression_feedback_suggester as sfs


def hit(hit_id, path, *, batch="b1", rule="r1", line=1):
    return {
        "hit_id": hit_id,
        "batch_id": batch,
        "rule_id": rule,
        "path": path,
        "line": line,
        "observed_at": "2026-11-20T00:00:00+00:00",
    }


def sample(hit_id, *, marked_by="alice"):
    return {"hit_id": hit_id, "marked_by": marked_by}


def suggester(**kwargs):
    return sfs.SuppressionFeedbackSuggester(**kwargs)


def test_ingest_hits_sets_visible_status_and_stats():
    obj = suggester()
    accepted = obj.ingest_hits([
        hit("h1", "src/a.py", batch="b1"),
        hit("h2", "src/b.py", batch="b2"),
        hit("h3", "tests/c.py", batch="b1", rule="r2"),
    ])
    assert accepted == 3
    assert [h.hit_id for h in obj.visible_hits()] == ["h1", "h2", "h3"]
    assert obj.suppressed_hits() == ()
    assert obj.stats() == {"hits": 3, "visible": 3, "suppressed": 0,
                           "samples": 0, "suggestions": 0, "pending": 0,
                           "active": 0, "revoked": 0}


def test_ingest_hits_rejects_duplicates_and_invalid_records():
    obj = suggester()
    obj.ingest_hits([hit("h1", "src/a.py")])
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.ingest_hits([hit("h1", "src/a.py")])
    assert exc.value.code == "FEEDBACK_HIT_DUPLICATE"
    bad = hit("h2", "src/b.py")
    bad.pop("rule_id")
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.ingest_hits([bad])
    assert exc.value.code == "FEEDBACK_HIT_INVALID"
    assert exc.value.detail == 0
    assert obj.stats()["hits"] == 1


def test_pattern_for_uses_rule_and_directory():
    obj = suggester()
    assert obj.pattern_for("r1", "src/a.py") == "r1::src"
    assert obj.pattern_for("r1", "a.py") == "r1::."
    assert obj.pattern_for("r1", "./src/sub/b.py") == "r1::src/sub"


def test_samples_reach_threshold_and_create_suggestion():
    obj = suggester(min_samples=3, min_precision=0.7)
    obj.ingest_hits([
        hit("h1", "src/a.py"), hit("h2", "src/b.py"),
        hit("h3", "src/c.py"), hit("h4", "src/d.py"),
    ])
    obj.add_sample(sample("h1"))
    obj.add_sample(sample("h2"))
    outcome = obj.add_sample(sample("h3"))
    assert outcome.sample_id == "sam-3"
    assert outcome.pattern == "r1::src"
    assert outcome.sample_count == 3
    assert outcome.precision == 0.75
    assert outcome.suggestion_id == "sug-1"
    assert outcome.created is True
    assert obj.stats()["samples"] == 3
    assert obj.stats()["suggestions"] == 1
    assert obj.stats()["pending"] == 1


def test_distinct_patterns_create_distinct_suggestions():
    obj = suggester(min_samples=1, min_precision=0.0)
    obj.ingest_hits([hit("h1", "src/a.py"), hit("h2", "tests/b.py")])
    obj.add_sample(sample("h1"))
    obj.add_sample(sample("h2"))
    listed = obj.suggestions()
    assert [s.sid for s in listed] == ["sug-1", "sug-2"]
    assert [s.pattern for s in listed] == ["r1::src", "r1::tests"]


def test_add_sample_rejects_unknown_duplicate_and_invalid():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py"), hit("h2", "src/b.py")])
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.add_sample(sample("h9"))
    assert exc.value.code == "FEEDBACK_SAMPLE_UNKNOWN_HIT"
    obj.add_sample(sample("h1"))
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.add_sample(sample("h1"))
    assert exc.value.code == "FEEDBACK_SAMPLE_DUPLICATE"
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.add_sample(sample("h2", marked_by="   "))
    assert exc.value.code == "FEEDBACK_SAMPLE_INVALID"
    assert obj.stats()["samples"] == 1


def test_suggestion_fields_after_creation():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py")])
    obj.add_sample(sample("h1"))
    sug = obj.suggestion("sug-1")
    assert sug.rule_id == "r1"
    assert sug.pattern == "r1::src"
    assert sug.sample_count == 1
    assert sug.hit_count == 1
    assert sug.precision == 1.0
    assert sug.status == "pending"
    assert sug.created_seq == 1
    assert sug.applied_by == ""
    assert sug.revoked_by == ""


def test_apply_requires_confirmation_token_then_operator():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py")])
    obj.add_sample(sample("h1"))
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.prepare_apply("ghost")
    assert exc.value.code == "FEEDBACK_SUGGESTION_UNKNOWN"
    token = obj.prepare_apply("sug-1")
    assert isinstance(token, str) and token
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.apply("sug-1", token="wrong-token", operator="alice")
    assert exc.value.code == "FEEDBACK_CONFIRMATION_INVALID"
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.apply("sug-1", token=token, operator="   ")
    assert exc.value.code == "FEEDBACK_OPERATOR_REQUIRED"
    assert obj.suggestion("sug-1").status == "pending"


def test_apply_activates_and_suppresses_matching_hits():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py"), hit("h2", "src/b.py")])
    obj.add_sample(sample("h1"))
    token = obj.prepare_apply("sug-1")
    active = obj.apply("sug-1", token=token, operator="alice")
    assert active.status == "active"
    assert active.applied_by == "alice"
    assert active.applied_seq == 1
    assert [h.hit_id for h in obj.suppressed_hits()] == ["h1", "h2"]
    assert obj.visible_hits() == ()
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.apply("sug-1", token=token, operator="alice")
    assert exc.value.code == "FEEDBACK_NOT_PENDING"
    assert obj.stats()["active"] == 1
    assert obj.stats()["suppressed"] == 2


def test_preview_lists_hits_without_suppressing():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py", batch="b1"),
                     hit("h2", "src/b.py", batch="b2"),
                     hit("h3", "tests/c.py", batch="b3")])
    obj.add_sample(sample("h1"))
    preview = obj.preview("sug-1")
    assert preview.pattern == "r1::src"
    assert preview.count == 2
    assert preview.hit_ids == ("h1", "h2")
    assert preview.batches == ("b1", "b2")
    assert [h.hit_id for h in obj.visible_hits()] == ["h1", "h2", "h3"]
    assert obj.suggestion("sug-1").status == "pending"


def test_suggestion_only_covers_its_own_directory():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py"),
                     hit("h2", "src/sub/b.py"),
                     hit("h3", "tests/c.py")])
    obj.add_sample(sample("h1"))
    token = obj.prepare_apply("sug-1")
    obj.apply("sug-1", token=token, operator="alice")
    assert [h.hit_id for h in obj.suppressed_hits()] == ["h1"]
    assert [h.hit_id for h in obj.visible_hits()] == ["h2", "h3"]


def test_revoke_restores_suppressed_hits():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py", batch="b1"),
                     hit("h2", "src/b.py", batch="b2")])
    obj.add_sample(sample("h1"))
    token = obj.prepare_apply("sug-1")
    obj.apply("sug-1", token=token, operator="alice")
    report = obj.revoke("sug-1", operator="bob", reason="too broad")
    revoked = obj.suggestion("sug-1")
    assert revoked.status == "revoked"
    assert revoked.revoked_by == "bob"
    assert revoked.revoked_reason == "too broad"
    assert report.restored == 2
    assert [h.hit_id for h in obj.visible_hits()] == ["h1", "h2"]
    assert obj.suppressed_hits() == ()
    assert obj.stats()["active"] == 0
    assert obj.stats()["revoked"] == 1


def test_revoke_rejects_pending_and_missing_operator_or_reason():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py")])
    obj.add_sample(sample("h1"))
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.revoke("sug-1", operator="bob", reason="cleanup")
    assert exc.value.code == "FEEDBACK_NOT_ACTIVE"
    token = obj.prepare_apply("sug-1")
    obj.apply("sug-1", token=token, operator="alice")
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.revoke("sug-1", operator="  ", reason="cleanup")
    assert exc.value.code == "FEEDBACK_OPERATOR_REQUIRED"
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.revoke("sug-1", operator="bob", reason=" ")
    assert exc.value.code == "FEEDBACK_REASON_REQUIRED"
    assert obj.suggestion("sug-1").status == "active"
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.revoke("ghost", operator="bob", reason="cleanup")
    assert exc.value.code == "FEEDBACK_SUGGESTION_UNKNOWN"


def test_concurrent_samples_produce_one_suggestion_per_pattern():
    obj = suggester(min_samples=1, min_precision=0.0)
    obj.ingest_hits([hit("h%d" % i, "src/a%d.py" % i, rule="r%d" % i)
                     for i in range(4)])
    errors = []

    def worker(index):
        try:
            obj.add_sample(sample("h%d" % index, marked_by="user%d" % index))
        except Exception as err:  # pragma: no cover - surfaced by assertion
            errors.append(err)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert obj.stats()["samples"] == 4
    assert obj.stats()["suggestions"] == 4
    patterns = sorted(s.pattern for s in obj.suggestions())
    assert patterns == ["r0::src", "r1::src", "r2::src", "r3::src"]


def test_snapshot_restore_roundtrip():
    obj = suggester(min_samples=1)
    obj.ingest_hits([hit("h1", "src/a.py", batch="b1"),
                     hit("h2", "src/b.py", batch="b2")])
    obj.add_sample(sample("h1"))
    token = obj.prepare_apply("sug-1")
    obj.apply("sug-1", token=token, operator="alice")
    snap = obj.snapshot()
    clone = suggester(min_samples=1)
    clone.restore(snap)
    assert clone.stats() == obj.stats()
    assert clone.suggestions() == obj.suggestions()
    assert [h.hit_id for h in clone.visible_hits()] == []
    assert [h.hit_id for h in clone.suppressed_hits()] == ["h1", "h2"]
    stats_before = obj.stats()
    with pytest.raises(sfs.FeedbackError) as exc:
        obj.restore({"hits": "invalid"})
    assert exc.value.code == "FEEDBACK_SNAPSHOT_INVALID"
    assert obj.stats() == stats_before
