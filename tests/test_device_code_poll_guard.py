import threading

import pytest

import device_code_poll_guard as dg

DEV_A = "d" * 20
DEV_B = "e" * 20
DEV_C = "f" * 20
CODE_A = "WDJB-MJHT"
CODE_B = "PQRS-TUVW"
CODE_C = "JKLM-NPQR"


def guard(**kwargs):
    return dg.DeviceCodePollGuard(**kwargs)


def test_normalize_user_code_formats_and_validates():
    g = guard()
    assert g.normalize_user_code("wdjb mjht") == CODE_A
    assert g.normalize_user_code(" WDJB-MJHT ") == CODE_A
    assert g.normalize_user_code("wdjbmjht") == CODE_A
    for bad in ("WDJB-MJH", "WDJB-MJHI", "WDJB-MJHTX", "", "WDJB_MJHT"):
        with pytest.raises(dg.DeviceCodePollError) as exc:
            g.normalize_user_code(bad)
        assert exc.value.code == "DCP_USER_CODE_INVALID"


def test_issue_returns_protocol_fields_and_enforces_capacity():
    g = guard(max_pending_per_client=2, interval=5, expires_in=600)
    out = g.issue("cli-1", ["openid", "profile"], now=100)
    assert out["user_code"] == g.normalize_user_code(out["user_code"])
    assert len(out["user_code"]) == 9
    assert len(out["device_code"]) >= 32
    assert out["interval"] == 5
    assert out["expires_at"] == 700
    assert out["scope"] == ["openid", "profile"]
    assert out["verification_uri"].startswith("https://")
    second = g.issue("cli-1", ["read"], now=100,
                     device_code=DEV_A, user_code=CODE_A)
    assert second["user_code"] == CODE_A
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.issue("cli-1", ["read"], now=100)
    assert exc.value.code == "DCP_CAPACITY_EXCEEDED"
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.issue("cli-2", ["read"], now=100,
                device_code=DEV_B, user_code=CODE_A)
    assert exc.value.code == "DCP_USER_CODE_TAKEN"


def test_approve_unknown_user_code_counts_failure():
    g = guard()
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.approve("ZZZZ-ZZZZ", subject="alice", now=0)
    assert exc.value.code == "DCP_USER_CODE_UNKNOWN"
    assert g.stats()["verify_failures"] == 1


def test_approve_binds_first_subject_and_is_idempotent():
    g = guard()
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    first = g.approve("wdjb mjht", subject="alice", now=10)
    assert first["status"] == "approved"
    assert first["already_approved"] is False
    assert first["device_code"] == DEV_A
    again = g.approve(CODE_A, subject="alice", now=11)
    assert again["already_approved"] is True
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.approve(CODE_A, subject="bob", now=12)
    assert exc.value.code == "DCP_SUBJECT_MISMATCH"
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.deny(CODE_A, now=13)
    assert exc.value.code == "DCP_ALREADY_APPROVED"


def test_approve_rejects_expired_user_code():
    g = guard(expires_in=60)
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    assert g.approve(CODE_A, subject="alice", now=59)["status"] == "approved"
    g.issue("cli-2", ["read"], now=0, device_code=DEV_B, user_code=CODE_B)
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.approve(CODE_B, subject="bob", now=60)
    assert exc.value.code == "DCP_EXPIRED"


def test_deny_is_terminal_and_poll_reports_access_denied():
    g = guard()
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    assert g.deny(CODE_A, now=5) is True
    assert g.deny(CODE_A, now=6) is False
    out = g.poll(DEV_A, client_id="cli-1", now=20)
    assert out["status"] == "access_denied"
    assert "access_token" not in out
    assert g.stats()["denied"] == 1


def test_poll_slow_down_raises_interval_then_pends():
    g = guard(interval=5, slow_down_step=5)
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    first = g.poll(DEV_A, client_id="cli-1", now=1)
    assert first["status"] == "slow_down"
    assert first["interval"] == 10
    assert first["retry_after"] == 10
    second = g.poll(DEV_A, client_id="cli-1", now=11)
    assert second["status"] == "authorization_pending"
    assert second["retry_after"] == 10
    assert g.stats()["slow_downs"] == 1


def test_poll_pending_at_exact_interval_boundary():
    g = guard(interval=5)
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    out = g.poll(DEV_A, client_id="cli-1", now=5)
    assert out["status"] == "authorization_pending"
    assert out["retry_after"] == 5
    assert out["interval"] == 5
    too_fast = g.poll(DEV_A, client_id="cli-1", now=9)
    assert too_fast["status"] == "slow_down"


def test_poll_rejects_unknown_device_code_and_client_mismatch():
    g = guard()
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.poll("nope", client_id="cli-1", now=0)
    assert exc.value.code == "DCP_DEVICE_CODE_UNKNOWN"
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.poll(DEV_A, client_id="cli-2", now=100)
    assert exc.value.code == "DCP_CLIENT_MISMATCH"


def test_poll_issues_bound_token_exactly_once():
    g = guard()
    g.issue("cli-1", ["read", "write"], now=0,
            device_code=DEV_A, user_code=CODE_A)
    g.approve(CODE_A, subject="alice", now=1)
    out = g.poll(DEV_A, client_id="cli-1", now=100)
    assert out["status"] == "token"
    assert out["token_type"] == "Bearer"
    assert out["expires_in"] == 300
    assert out["scope"] == ["read", "write"]
    assert out["access_token"] != out["refresh_token"]
    listed = g.bindings()
    assert len(listed) == 1
    assert listed[0]["subject"] == "alice"
    assert listed[0]["client_id"] == "cli-1"
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.poll(DEV_A, client_id="cli-1", now=101)
    assert exc.value.code == "DCP_TOKEN_CONSUMED"


def test_poll_reports_expired_device_code():
    g = guard(expires_in=60)
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    out = g.poll(DEV_A, client_id="cli-1", now=60)
    assert out["status"] == "expired_token"
    assert "access_token" not in out


def test_refresh_rotates_and_rejects_replay():
    g = guard()
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    g.approve(CODE_A, subject="alice", now=1)
    token = g.poll(DEV_A, client_id="cli-1", now=100)
    renewed = g.refresh(token["refresh_token"], client_id="cli-1", now=150)
    assert renewed["access_token"] != token["access_token"]
    assert renewed["refresh_token"] != token["refresh_token"]
    assert renewed["expires_in"] == 300
    assert renewed["scope"] == ["read"]
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.refresh(token["refresh_token"], client_id="cli-1", now=151)
    assert exc.value.code == "DCP_REFRESH_REUSED"
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.refresh(renewed["refresh_token"], client_id="cli-2", now=152)
    assert exc.value.code == "DCP_CLIENT_MISMATCH"


def test_revoke_kills_the_token_family():
    g = guard()
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    g.approve(CODE_A, subject="alice", now=1)
    token = g.poll(DEV_A, client_id="cli-1", now=100)
    assert g.revoke(token["access_token"], now=160) is True
    assert g.revoke(token["access_token"], now=161) is False
    assert g.bindings() == []
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.refresh(token["refresh_token"], client_id="cli-1", now=162)
    assert exc.value.code == "DCP_TOKEN_REVOKED"
    assert g.stats()["revocations"] == 1


def test_purge_and_snapshot_restore():
    g = guard(expires_in=10)
    g.issue("cli-1", ["a"], now=0, device_code=DEV_A, user_code=CODE_A)
    g.issue("cli-2", ["b"], now=0, device_code=DEV_B, user_code=CODE_B)
    assert g.purge(now=5) == 0
    assert g.purge(now=10) == 2
    assert g.purge(now=11) == 0
    snap = g.snapshot()
    g.issue("cli-3", ["c"], now=20, device_code=DEV_C, user_code=CODE_C)
    assert g.stats()["issued"] == 3
    g.restore(snap)
    assert g.stats()["issued"] == 2
    with pytest.raises(dg.DeviceCodePollError) as exc:
        g.restore({"codes": []})
    assert exc.value.code == "DCP_SNAPSHOT_INVALID"
    assert g.stats()["issued"] == 2


def test_concurrent_poll_issues_exactly_one_token():
    g = guard()
    g.issue("cli-1", ["read"], now=0, device_code=DEV_A, user_code=CODE_A)
    g.approve(CODE_A, subject="alice", now=1)
    results = []
    errors = []
    lock = threading.Lock()

    def worker():
        try:
            out = g.poll(DEV_A, client_id="cli-1", now=100)
        except dg.DeviceCodePollError as exc:
            with lock:
                errors.append(exc.code)
        else:
            with lock:
                results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 1
    assert results[0]["status"] == "token"
    assert sorted(errors) == ["DCP_TOKEN_CONSUMED"] * 7
    assert g.stats()["tokens_issued"] == 1
    assert len(g.bindings()) == 1
