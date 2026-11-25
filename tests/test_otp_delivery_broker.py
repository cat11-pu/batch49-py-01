import copy
import threading

import pytest

import otp_delivery_broker as ob

TARGET = "alice@example.com"


def broker(**kwargs):
    return ob.OtpDeliveryBroker(**kwargs)


def test_request_creates_batch_on_primary_channel():
    step = broker()
    batch = step.request_code(TARGET, tenant="t1", now=0)
    assert batch.batch_id == "OTP-000001"
    assert (batch.target, batch.tenant, batch.purpose) == (TARGET, "t1", "login")
    assert batch.channel == "email"
    assert batch.code != ""
    assert (batch.created_at, batch.expires_at) == (0, 5)
    assert (batch.attempts_left, batch.status) == (3, "sent")
    assert batch.fallback_reason is None
    assert step.stats()["requested"] == 1
    status = step.channel_status()
    assert status["email"] == {"enabled": True, "sent": 1, "failed": 0}
    assert status["sms"] == {"enabled": True, "sent": 0, "failed": 0}
    second = step.request_code("bob@example.com", tenant="t1", now=8)
    assert second.batch_id == "OTP-000002"


def test_suppression_window_reuses_the_original_batch():
    step = broker()
    first = step.request_code(TARGET, tenant="t1", now=0)
    again = step.request_code(TARGET, tenant="t1", now=1)
    assert again.batch_id == first.batch_id
    assert again.code == first.code
    assert step.stats()["reused"] == 1
    assert step.target_usage(TARGET, now=1)["sent"] == 1
    assert step.channel_status()["email"]["sent"] == 1


def test_target_quota_is_enforced_per_window():
    step = broker(target_limit=2, tenant_limit=10)
    step.request_code(TARGET, tenant="t1", now=0)
    step.request_code(TARGET, tenant="t1", now=4)
    with pytest.raises(ob.OtpError) as exc:
        step.request_code(TARGET, tenant="t1", now=8)
    assert exc.value.code == "OTP_TARGET_QUOTA"
    assert step.stats()["requested"] == 2


def test_tenant_quota_is_enforced_per_window():
    step = broker(tenant_limit=2, target_limit=5)
    step.request_code("a@example.com", tenant="t1", now=0)
    step.request_code("b@example.com", tenant="t1", now=4)
    with pytest.raises(ob.OtpError) as exc:
        step.request_code("c@example.com", tenant="t1", now=8)
    assert exc.value.code == "OTP_TENANT_QUOTA"
    other = step.request_code("d@example.com", tenant="t2", now=8)
    assert other.batch_id == "OTP-000003"


def test_disabled_primary_channel_falls_back_to_sms():
    step = broker()
    step.set_channel_enabled("email", False)
    batch = step.request_code(TARGET, tenant="t1", now=0)
    assert (batch.channel, batch.fallback_reason) == ("sms", "primary_disabled")
    status = step.channel_status()
    assert status["email"]["sent"] == 0
    assert status["sms"]["sent"] == 1


def test_both_channels_disabled_is_refused():
    step = broker()
    step.set_channel_enabled("email", False)
    step.set_channel_enabled("sms", False)
    with pytest.raises(ob.OtpError) as exc:
        step.request_code(TARGET, tenant="t1", now=0)
    assert exc.value.code == "OTP_CHANNEL_UNAVAILABLE"
    assert step.stats()["requested"] == 0
    assert step.target_usage(TARGET, now=0)["sent"] == 0


def test_verify_correct_code_consumes_the_batch():
    step = broker()
    batch = step.request_code(TARGET, tenant="t1", now=0)
    result = step.verify_code(TARGET, batch.code, now=1)
    assert (result.status, result.batch_id, result.reason) == (
        "verified", "OTP-000001", "code_matched")
    assert result.attempts_left == 3
    assert step.stats()["verified"] == 1
    with pytest.raises(ob.OtpError) as exc:
        step.verify_code(TARGET, batch.code, now=2)
    assert exc.value.code == "OTP_BATCH_UNKNOWN"
    with pytest.raises(ob.OtpError) as exc:
        step.verify_code("nobody@example.com", "WRONG", now=2)
    assert exc.value.code == "OTP_BATCH_UNKNOWN"


def test_wrong_code_burns_attempts_until_exhausted():
    step = broker()
    batch = step.request_code(TARGET, tenant="t1", now=0)
    first = step.verify_code(TARGET, "WRONG-1", now=1)
    second = step.verify_code(TARGET, "WRONG-2", now=2)
    third = step.verify_code(TARGET, "WRONG-3", now=3)
    assert (first.status, first.attempts_left) == ("mismatch", 2)
    assert (second.status, second.attempts_left) == ("mismatch", 1)
    assert (third.status, third.attempts_left) == ("exhausted", 0)
    assert third.reason == "attempts_exhausted"
    after = step.verify_code(TARGET, batch.code, now=4)
    assert (after.status, after.attempts_left) == ("exhausted", 0)
    assert step.stats()["mismatches"] == 3


def test_expired_code_is_reported_and_then_unknown():
    step = broker()
    batch = step.request_code(TARGET, tenant="t1", now=0)
    result = step.verify_code(TARGET, batch.code, now=5)
    assert (result.status, result.reason, result.attempts_left) == (
        "expired", "code_expired", 0)
    assert step.stats()["expired"] == 1
    with pytest.raises(ob.OtpError) as exc:
        step.verify_code(TARGET, batch.code, now=6)
    assert exc.value.code == "OTP_BATCH_UNKNOWN"


def test_resend_creates_a_new_batch_and_charges_quota():
    step = broker()
    first = step.request_code(TARGET, tenant="t1", now=0)
    second = step.resend(TARGET, now=1)
    assert second.batch_id == "OTP-000002"
    assert (second.created_at, second.expires_at) == (1, 6)
    assert second.channel == "email"
    assert second.code != first.code
    assert step.stats()["resends"] == 1
    assert step.stats()["requested"] == 1
    assert step.target_usage(TARGET, now=1)["sent"] == 2
    with pytest.raises(ob.OtpError) as exc:
        step.resend("nobody@example.com", now=1)
    assert exc.value.code == "OTP_BATCH_UNKNOWN"


def test_channel_quota_is_enforced_per_window():
    step = broker(channel_limit=2, target_limit=10, tenant_limit=10)
    step.request_code("a@example.com", tenant="t1", now=0)
    step.request_code("b@example.com", tenant="t1", now=4)
    with pytest.raises(ob.OtpError) as exc:
        step.request_code("c@example.com", tenant="t1", now=8)
    assert exc.value.code == "OTP_CHANNEL_QUOTA"
    assert step.stats()["requested"] == 2


def test_stats_track_each_operation():
    step = broker()
    first = step.request_code("a@example.com", tenant="t1", now=0)
    step.request_code("a@example.com", tenant="t1", now=1)
    step.verify_code("a@example.com", first.code, now=2)
    step.request_code("b@example.com", tenant="t1", now=6)
    step.verify_code("b@example.com", "WRONG", now=7)
    step.resend("b@example.com", now=8)
    assert step.purge_expired(now=20) == 3
    assert step.purge_expired(now=21) == 0
    assert step.stats() == {"requested": 2, "reused": 1, "verified": 1,
                            "mismatches": 1, "expired": 0, "resends": 1,
                            "purged": 3}


def test_snapshot_is_detached_and_restore_roundtrips():
    step = broker()
    batch = step.request_code(TARGET, tenant="t1", now=0)
    step.verify_code(TARGET, "WRONG", now=1)
    step.set_channel_enabled("sms", False)
    state = step.snapshot()
    assert set(state) == {"version", "batches", "channels", "sequence",
                          "quotas", "counters"}
    pristine = copy.deepcopy(state)
    state["batches"].clear()
    state["counters"]["requested"] = 99
    assert step.stats()["requested"] == 1
    assert step.channel_status()["sms"]["enabled"] is False
    clone = broker()
    clone.restore(pristine)
    assert clone.channel_status()["sms"]["enabled"] is False
    assert clone.stats()["mismatches"] == 1
    result = clone.verify_code(TARGET, batch.code, now=2)
    assert (result.status, result.batch_id) == ("verified", "OTP-000001")
    assert clone.stats()["verified"] == 1


def test_restore_rejects_malformed_snapshot():
    step = broker()
    step.request_code(TARGET, tenant="t1", now=0)
    good = step.snapshot()
    bad_states = [
        None,
        {},
        dict(good, version=2),
        dict(good, batches={}),
        dict(good, channels=[]),
        dict(good, quotas={}),
        dict(good, counters={}),
    ]
    for bad in bad_states:
        with pytest.raises(ob.OtpError) as exc:
            step.restore(bad)
        assert exc.value.code == "OTP_SNAPSHOT_INVALID"
    assert step.request_code("b@example.com", tenant="t1",
                             now=6).batch_id == "OTP-000002"


def test_concurrent_requests_for_same_target_share_one_batch():
    step = broker()
    batch_ids = []
    errors = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait(timeout=5)
        try:
            batch = step.request_code(TARGET, tenant="t1", purpose="login",
                                      now=0)
            batch_ids.append(batch.batch_id)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert set(batch_ids) == {"OTP-000001"}
    assert step.stats()["requested"] == 1
    assert step.stats()["reused"] == 5
    assert step.channel_status()["email"]["sent"] == 1
    assert step.target_usage(TARGET, now=0) == {"sent": 1, "remaining": 2,
                                                "window_index": 0}
