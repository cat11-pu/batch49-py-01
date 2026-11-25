import copy

import pytest

import mfa_recovery_caseflow as mfa

NOW = 1000
WAIT = 4600
EXPIRES = 6400


def flow(**kwargs):
    return mfa.MfaRecoveryCaseflow(**kwargs)


def seeded(**kwargs):
    f = flow(**kwargs)
    f.register_account(account="alice", factors=["sms", "totp"], now=NOW)
    return f


def open_low(f):
    return f.open_case(account="alice", risk="low", now=NOW)


def open_high(f):
    return f.open_case(account="alice", risk="high", now=NOW)


def test_open_case_sets_wait_window_and_review_quota():
    f = seeded()
    case = open_low(f)
    assert case.case_id == "mfa-0001"
    assert case.account == "alice"
    assert case.risk == "low"
    assert case.status == "waiting"
    assert case.required_reviews == 1
    assert case.wait_until == NOW
    assert case.expires_at == NOW + 1800
    assert case.reviewers == ()
    assert case.approvals == ()
    assert case.rejections == ()
    assert f.stats() == {"opened": 1, "completed": 0, "expired": 0,
                         "rejected": 0, "reviews": 0,
                         "revoked_factors": 0, "frozen": 1}


def test_second_open_case_is_refused_until_first_closes():
    f = seeded()
    first = open_low(f)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        open_low(f)
    assert exc.value.code == "MFA_CASE_DUPLICATE"

    f.assign_reviewers(case_id=first.case_id, reviewers=["r1"], now=NOW)
    f.review(case_id=first.case_id, reviewer="r1", approve=True, now=NOW)
    f.complete(case_id=first.case_id, new_factors=["backup"], now=NOW)

    second = open_low(f)
    assert second.case_id == "mfa-0002"
    assert second.status == "waiting"


def test_bad_risk_blank_account_and_unknown_account_are_rejected():
    f = seeded()
    for kwargs in (
        dict(account="alice", risk="medium", now=NOW),
        dict(account="", risk="low", now=NOW),
        dict(account="   ", risk="low", now=NOW),
    ):
        with pytest.raises(mfa.MfaRecoveryError) as exc:
            f.open_case(**kwargs)
        assert exc.value.code == "MFA_INVALID_REQUEST"

    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.open_case(account="ghost", risk="low", now=NOW)
    assert exc.value.code == "MFA_ACCOUNT_NOT_FOUND"


def test_open_case_freezes_factor_changes_but_not_password_login():
    f = seeded()
    open_high(f)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.add_factor(account="alice", factor="backup", now=NOW)
    assert exc.value.code == "MFA_FACTORS_FROZEN"
    assert f.password_login(account="alice") is True
    assert f.stats()["frozen"] == 1

    previous = f.freeze_factors(account="alice", frozen=False, now=NOW)
    assert previous is True
    assert f.add_factor(account="alice", factor="backup", now=NOW) == (
        "backup", "sms", "totp")
    assert f.stats()["frozen"] == 0


def test_review_during_waiting_period_is_refused():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1"], now=NOW)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.review(case_id=case.case_id, reviewer="r1", approve=True,
                 now=WAIT - 1)
    assert exc.value.code == "MFA_CASE_WAIT_ACTIVE"
    assert f.stats()["reviews"] == 0
    assert f.get_case(case.case_id).approvals == ()


def test_unassigned_reviewer_and_unknown_case_are_refused():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1"], now=NOW)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.review(case_id=case.case_id, reviewer="r2", approve=True, now=WAIT)
    assert exc.value.code == "MFA_REVIEWER_NOT_ASSIGNED"
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.review(case_id="mfa-9999", reviewer="r1", approve=True, now=WAIT)
    assert exc.value.code == "MFA_CASE_NOT_FOUND"
    assert f.stats()["reviews"] == 0


def test_same_reviewer_cannot_vote_twice():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1", "r2"], now=NOW)
    f.review(case_id=case.case_id, reviewer="r1", approve=True, now=WAIT)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.review(case_id=case.case_id, reviewer="r1", approve=True, now=WAIT)
    assert exc.value.code == "MFA_REVIEW_DUPLICATE"
    assert f.get_case(case.case_id).approvals == ("r1",)
    assert f.stats()["reviews"] == 1


def test_high_risk_completes_after_two_approvals():
    f = seeded()
    case = open_high(f)
    assigned = f.assign_reviewers(case_id=case.case_id,
                                  reviewers=["r2", "r1", "r1"], now=NOW)
    assert assigned.reviewers == ("r1", "r2")
    assert assigned.required_reviews == 2

    after_one = f.review(case_id=case.case_id, reviewer="r2", approve=True,
                         now=WAIT)
    assert after_one.approvals == ("r2",)
    after_two = f.review(case_id=case.case_id, reviewer="r1", approve=True,
                         now=WAIT)
    assert after_two.approvals == ("r1", "r2")

    report = f.complete(case_id=case.case_id,
                        new_factors=["phone", "backup"], now=WAIT)
    assert report.revoked_factors == ("sms", "totp")
    assert report.added_factors == ("backup", "phone")
    assert report.audit_from == 1
    assert report.audit_to == 4
    assert f.get_case(case.case_id).status == "completed"
    assert f.stats()["completed"] == 1
    assert f.stats()["revoked_factors"] == 2


def test_complete_before_enough_approvals_is_refused():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1", "r2"], now=NOW)
    f.review(case_id=case.case_id, reviewer="r1", approve=True, now=WAIT)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.complete(case_id=case.case_id, new_factors=["backup"], now=WAIT)
    assert exc.value.code == "MFA_REVIEW_INCOMPLETE"
    assert f.get_case(case.case_id).status == "waiting"
    assert f.stats()["completed"] == 0


def test_complete_replaces_factors_and_audits_in_order():
    f = seeded()
    case = open_low(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1"], now=NOW)
    f.review(case_id=case.case_id, reviewer="r1", approve=True, now=NOW)
    report = f.complete(case_id=case.case_id,
                        new_factors=["backup", "phone"], now=NOW)
    assert report.revoked_factors == ("sms", "totp")
    assert report.added_factors == ("backup", "phone")
    assert (report.audit_from, report.audit_to) == (1, 4)

    assert f.add_factor(account="alice", factor="yubikey", now=NOW + 1) == (
        "backup", "phone", "yubikey")
    assert f.stats()["frozen"] == 0


def test_reject_vote_closes_case_and_unfreezes():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1", "r2"], now=NOW)
    rejected = f.review(case_id=case.case_id, reviewer="r1", approve=False,
                        now=WAIT)
    assert rejected.status == "rejected"
    assert rejected.rejections == ("r1",)
    assert rejected.approvals == ()
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        f.complete(case_id=case.case_id, new_factors=["backup"], now=WAIT)
    assert exc.value.code == "MFA_CASE_CLOSED"
    assert f.stats()["rejected"] == 1
    assert f.add_factor(account="alice", factor="backup", now=WAIT + 1) == (
        "backup", "sms", "totp")


def test_expire_only_closes_cases_at_or_past_deadline():
    f = seeded()
    case = open_high(f)
    assert f.expire(EXPIRES - 1) == ()
    assert f.get_case(case.case_id).status == "waiting"
    assert f.expire(EXPIRES) == (case.case_id,)
    assert f.get_case(case.case_id).status == "expired"
    assert f.expire(EXPIRES + 1) == ()
    assert f.stats()["expired"] == 1


def test_case_can_be_reopened_after_it_expires():
    f = seeded()
    first = open_high(f)
    f.expire(EXPIRES)
    assert f.get_case(first.case_id).status == "expired"
    second = f.open_case(account="alice", risk="low", now=EXPIRES)
    assert second.case_id == "mfa-0002"
    assert second.status == "waiting"


def test_snapshot_is_detached_and_restore_roundtrips():
    f = seeded()
    case = open_high(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1"], now=NOW)
    state = f.snapshot()
    assert set(state) == {"version", "cases", "accounts", "stats"}
    pristine = copy.deepcopy(state)

    state["cases"].clear()
    state["stats"]["opened"] = 99
    assert f.get_case(case.case_id).status == "waiting"
    assert f.stats()["opened"] == 1

    clone = flow()
    clone.restore(pristine)
    restored = clone.get_case(case.case_id)
    assert restored.wait_until == WAIT
    assert restored.reviewers == ("r1",)
    with pytest.raises(mfa.MfaRecoveryError) as exc:
        clone.add_factor(account="alice", factor="backup", now=NOW)
    assert exc.value.code == "MFA_FACTORS_FROZEN"
    assert clone.stats()["opened"] == 1
    assert clone.stats()["frozen"] == 1


def test_stats_track_completed_and_rejected_cases_exactly():
    f = seeded()
    case = open_low(f)
    f.assign_reviewers(case_id=case.case_id, reviewers=["r1"], now=NOW)
    f.review(case_id=case.case_id, reviewer="r1", approve=True, now=NOW)
    f.complete(case_id=case.case_id, new_factors=["backup"], now=NOW)

    f.register_account(account="bob", factors=["sms"], now=NOW)
    second = f.open_case(account="bob", risk="high", now=NOW)
    f.assign_reviewers(case_id=second.case_id, reviewers=["r1"], now=NOW)
    f.review(case_id=second.case_id, reviewer="r1", approve=False, now=WAIT)

    assert f.stats() == {"opened": 2, "completed": 1, "expired": 0,
                         "rejected": 1, "reviews": 2,
                         "revoked_factors": 2, "frozen": 0}
