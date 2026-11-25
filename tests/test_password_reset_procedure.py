import copy
import threading

import pytest

import password_reset_procedure as pr


def procedure(**kwargs):
    return pr.PasswordResetProcedure(**kwargs)


def test_open_case_creates_collecting_case():
    proc = procedure()
    case = proc.open_case("alice", now=3)
    assert case.case_id == "RST-0001"
    assert (case.account, case.status, case.level) == ("alice", "collecting", 0)
    assert (case.required_level, case.evidence) == (2, ())
    assert (case.opened_at, case.expires_at, case.cooldown_until) == (3, 13, 9)
    assert (case.approved_until, case.reviewer) == (None, None)
    assert proc.stats()["opened"] == 1


def test_case_ids_increment_per_account():
    proc = procedure()
    first = proc.open_case("alice", now=0)
    second = proc.open_case("bob", now=1)
    assert (first.case_id, second.case_id) == ("RST-0001", "RST-0002")
    assert proc.stats()["opened"] == 2


def test_case_capacity_is_enforced():
    proc = procedure(max_cases=1)
    proc.open_case("alice", now=0)
    with pytest.raises(pr.ResetError) as exc:
        proc.open_case("bob", now=0)
    assert exc.value.code == "RESET_CAPACITY"
    assert proc.stats()["opened"] == 1


def test_evidence_is_deduplicated_and_validated():
    proc = procedure()
    proc.open_case("alice", now=0)
    first = proc.submit_evidence("alice", "email", now=1)
    assert (first.evidence, first.level) == (("email",), 1)
    duplicate = proc.submit_evidence("alice", "email", now=2)
    assert (duplicate.evidence, duplicate.level) == (("email",), 1)
    assert proc.stats()["evidence"] == 1
    second = proc.submit_evidence("alice", "phone", now=3)
    assert (second.evidence, second.level) == (("email", "phone"), 2)
    assert proc.stats()["evidence"] == 2
    with pytest.raises(pr.ResetError) as exc:
        proc.submit_evidence("alice", "fax", now=4)
    assert exc.value.code == "RESET_EVIDENCE_INVALID"
    with pytest.raises(pr.ResetError) as exc:
        proc.submit_evidence("ghost", "email", now=4)
    assert exc.value.code == "RESET_CASE_UNKNOWN"


def test_review_downgrades_when_evidence_is_missing():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    result = proc.review("alice", now=2)
    assert (result.status, result.granted_level, result.required_level,
            result.missing) == ("downgraded", 1, 2, ("phone",))
    assert proc.stats()["reviews"] == 1


def test_review_is_eligible_with_full_evidence():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.submit_evidence("alice", "phone", now=2)
    result = proc.review("alice", now=3)
    assert (result.status, result.granted_level) == ("eligible", 2)
    assert result.missing == ()


def test_approve_requires_review_and_reviewer():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.submit_evidence("alice", "phone", now=2)
    with pytest.raises(pr.ResetError) as exc:
        proc.approve("alice", "ops", now=3)
    assert exc.value.code == "RESET_NOT_REVIEWED"
    proc.review("alice", now=4)
    with pytest.raises(pr.ResetError) as exc:
        proc.approve("alice", "   ", now=5)
    assert exc.value.code == "RESET_INVALID"
    case = proc.approve("alice", "ops-lee", now=6)
    assert (case.status, case.reviewer) == ("approved", "ops-lee")
    assert case.approved_until == 10
    assert proc.stats()["approvals"] == 1


def test_approve_rejects_downgraded_case_until_evidence_is_added():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.review("alice", now=2)
    with pytest.raises(pr.ResetError) as exc:
        proc.approve("alice", "ops", now=3)
    assert exc.value.code == "RESET_INSUFFICIENT_EVIDENCE"
    proc.submit_evidence("alice", "phone", now=4)
    with pytest.raises(pr.ResetError) as exc:
        proc.approve("alice", "ops", now=5)
    assert exc.value.code == "RESET_NOT_REVIEWED"
    proc.review("alice", now=6)
    assert proc.approve("alice", "ops", now=7).status == "approved"


def test_complete_requires_approval():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.submit_evidence("alice", "phone", now=2)
    proc.review("alice", now=3)
    with pytest.raises(pr.ResetError) as exc:
        proc.complete("alice", now=4)
    assert exc.value.code == "RESET_NOT_APPROVED"
    assert proc.stats()["completed"] == 0


def test_complete_invalidates_only_that_account_sessions():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.register_session("alice", "sess-1")
    proc.register_session("alice", "sess-2")
    proc.register_session("bob", "sess-3")
    proc.submit_evidence("alice", "email", now=1)
    proc.submit_evidence("alice", "phone", now=2)
    proc.review("alice", now=3)
    proc.approve("alice", "ops", now=4)
    result = proc.complete("alice", now=5)
    assert (result.status, result.case_id) == ("completed", "RST-0001")
    assert (result.sessions_invalidated, result.notified_via) == (2, "email")
    assert proc.active_sessions("alice") == []
    assert proc.active_sessions("bob") == ["sess-3"]
    assert proc.stats()["sessions_invalidated"] == 2
    assert proc.stats()["completed"] == 1


def test_expire_marks_case_and_blocks_later_review():
    proc = procedure(validity=5)
    proc.open_case("alice", now=0)
    assert proc.expire(now=4) == 0
    assert proc.expire(now=5) == 1
    assert proc.expire(now=6) == 0
    assert proc.stats()["expired"] == 1
    with pytest.raises(pr.ResetError) as exc:
        proc.review("alice", now=6)
    assert exc.value.code == "RESET_CASE_EXPIRED"


def test_stats_track_each_operation():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.submit_evidence("alice", "phone", now=2)
    proc.review("alice", now=3)
    proc.approve("alice", "ops", now=4)
    proc.register_session("alice", "s1")
    proc.complete("alice", now=5)
    proc.open_case("bob", now=6)
    assert proc.stats() == {"opened": 2, "evidence": 2, "reviews": 1,
                            "approvals": 1, "completed": 1, "expired": 0,
                            "sessions_invalidated": 1}


def test_snapshot_is_detached_and_restore_roundtrips():
    proc = procedure()
    proc.open_case("alice", now=0)
    proc.submit_evidence("alice", "email", now=1)
    proc.register_session("alice", "sess-1")
    state = proc.snapshot()
    assert set(state) == {"version", "cases", "sessions", "sequence",
                          "counters"}
    pristine = copy.deepcopy(state)
    state["cases"].clear()
    state["counters"]["opened"] = 99
    assert proc.active_sessions("alice") == ["sess-1"]
    assert proc.stats()["opened"] == 1
    clone = procedure()
    clone.restore(pristine)
    assert clone.active_sessions("alice") == ["sess-1"]
    case = clone.submit_evidence("alice", "phone", now=9)
    assert (case.evidence, case.level) == (("email", "phone"), 2)
    assert clone.stats()["evidence"] == 2
    assert clone.open_case("bob", now=10).case_id == "RST-0002"


def test_restore_rejects_malformed_snapshot():
    proc = procedure()
    proc.open_case("alice", now=0)
    good = proc.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, cases={}),
        dict(good, sessions=[]),
        dict(good, counters={}),
    ]
    for bad in bad_states:
        with pytest.raises(pr.ResetError) as exc:
            proc.restore(bad)
        assert exc.value.code == "RESET_SNAPSHOT_INVALID"
    assert proc.submit_evidence("alice", "email", now=1).level == 1


def test_concurrent_evidence_merges_without_duplicates():
    proc = procedure()
    proc.open_case("alice", now=0)
    kinds = ["email", "email", "phone", "phone", "manual", "manual"]
    errors = []
    barrier = threading.Barrier(len(kinds))

    def worker(kind):
        barrier.wait(timeout=5)
        try:
            proc.submit_evidence("alice", kind, now=1)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(kind,))
               for kind in kinds]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    result = proc.review("alice", now=2)
    assert (result.status, result.granted_level) == ("eligible", 3)
    assert result.missing == ()
    assert proc.stats()["evidence"] == 3
