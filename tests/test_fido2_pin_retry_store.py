import copy
import threading

import pytest

import fido2_pin_retry_store as fp

PIN = "246810"


def store(**kwargs):
    kwargs.setdefault("pin", PIN)
    return fp.Fido2PinRetryStore(**kwargs)


def test_initial_pin_state_and_empty_stats():
    key = store(max_retries=5)
    state = key.pin_status()
    assert (state.retries_left, state.hard_locked) == (5, False)
    assert key.list_credentials() == []
    assert key.device_info() == {"device_id": "key-0001",
                                 "firmware_generation": 1}
    assert key.stats() == {"verifies": 0, "failures": 0, "hard_locks": 0,
                           "resets": 0, "registered": 0, "deleted": 0,
                           "pin_changes": 0}


def test_correct_pin_reports_ok_and_restores_retry_budget():
    key = store(max_retries=3)
    miss = key.verify_pin("000000", now=0)
    assert (miss.status, miss.retries_left) == ("pin_invalid", 2)
    hit = key.verify_pin(PIN, now=1)
    assert (hit.status, hit.retries_left) == ("ok", 3)
    assert key.pin_status().retries_left == 3
    assert key.stats()["verifies"] == 1
    assert key.stats()["failures"] == 1


def test_wrong_pin_reaches_hard_lock_and_then_refuses():
    key = store(max_retries=3)
    seen = [key.verify_pin("111111", now=index) for index in range(3)]
    assert [attempt.status for attempt in seen] == ["pin_invalid",
                                                    "pin_invalid",
                                                    "hard_locked"]
    assert [attempt.retries_left for attempt in seen] == [2, 1, 0]
    assert key.pin_status().hard_locked is True
    with pytest.raises(fp.Fido2PinError) as exc:
        key.verify_pin("111111", now=4)
    assert exc.value.code == "PIN_HARD_LOCKED"
    assert key.stats()["failures"] == 3
    assert key.stats()["hard_locks"] == 1


def test_pin_format_errors_do_not_consume_retries():
    key = store(max_retries=4)
    for bad in ["123", "24781a", "2468 10", "1" * 64, ""]:
        with pytest.raises(fp.Fido2PinError) as exc:
            key.verify_pin(bad, now=0)
        assert exc.value.code == "PIN_FORMAT_INVALID"
    state = key.pin_status()
    assert (state.retries_left, state.hard_locked) == (4, False)
    assert key.stats()["failures"] == 0


def test_change_pin_requires_old_pin_and_resets_counters():
    key = store(max_retries=3)
    with pytest.raises(fp.Fido2PinError) as exc:
        key.change_pin("000000", "135790", now=0)
    assert exc.value.code == "PIN_INVALID"
    assert key.pin_status().retries_left == 2
    assert key.stats()["failures"] == 1
    assert key.change_pin(PIN, "135790", now=1) is None
    assert key.verify_pin("135790", now=2).status == "ok"
    assert key.pin_status().retries_left == 3
    stale = key.verify_pin(PIN, now=3)
    assert stale.status == "pin_invalid"
    assert key.stats()["pin_changes"] == 1


def test_change_pin_rejects_unchanged_or_malformed_new_pin():
    key = store()
    with pytest.raises(fp.Fido2PinError) as exc:
        key.change_pin(PIN, PIN, now=0)
    assert exc.value.code == "PIN_UNCHANGED"
    with pytest.raises(fp.Fido2PinError) as exc:
        key.change_pin(PIN, "12", now=0)
    assert exc.value.code == "PIN_FORMAT_INVALID"
    assert key.stats()["failures"] == 0
    assert key.stats()["pin_changes"] == 0


def test_register_and_list_keep_registration_order():
    key = store()
    first = key.register_credential("cred-b", "bob", rp_id="rp.example", now=10)
    second = key.register_credential("cred-a", "alice", now=11)
    assert first.credential_id == "cred-b"
    assert first.rp_id == "rp.example"
    assert first.created_at == 10
    assert second.rp_id == "default"
    rows = key.list_credentials()
    assert [row.credential_id for row in rows] == ["cred-b", "cred-a"]
    assert [(row.user_name, row.created_at) for row in rows] == [("bob", 10),
                                                                 ("alice", 11)]
    assert key.stats()["registered"] == 2


def test_duplicate_credential_is_refused():
    key = store()
    key.register_credential("cred-1", "alice", now=0)
    with pytest.raises(fp.Fido2PinError) as exc:
        key.register_credential("cred-1", "mallory", now=1)
    assert exc.value.code == "CREDENTIAL_DUPLICATE"
    assert len(key.list_credentials()) == 1
    assert key.stats()["registered"] == 1


def test_capacity_limit_and_delete_accounting():
    key = store(max_resident=2)
    key.register_credential("cred-a", "alice", now=0)
    key.register_credential("cred-b", "bob", now=1)
    with pytest.raises(fp.Fido2PinError) as exc:
        key.register_credential("cred-c", "carol", now=2)
    assert exc.value.code == "RESIDENT_FULL"
    assert key.delete_credential("cred-missing") is False
    assert key.stats()["deleted"] == 0
    assert key.delete_credential("cred-a") is True
    key.register_credential("cred-c", "carol", now=3)
    assert [row.credential_id for row in key.list_credentials()] == ["cred-b",
                                                                     "cred-c"]
    assert key.stats()["deleted"] == 1


def test_register_rejects_blank_fields():
    key = store()
    cases = [("", "alice", "default"), ("   ", "alice", "default"),
             ("cred-1", "", "default"), ("cred-1", "alice", "  ")]
    for credential_id, user_name, rp_id in cases:
        with pytest.raises(fp.Fido2PinError) as exc:
            key.register_credential(credential_id, user_name, rp_id=rp_id,
                                    now=0)
        assert exc.value.code == "CREDENTIAL_INVALID"
    assert key.stats()["registered"] == 0
    assert key.list_credentials() == []


def test_reset_clears_credentials_and_unlocks():
    key = store(max_retries=2)
    key.register_credential("cred-a", "alice", now=0)
    key.register_credential("cred-b", "bob", now=1)
    key.verify_pin("000000", now=2)
    assert key.reset(now=3) == 2
    state = key.pin_status()
    assert (state.retries_left, state.hard_locked) == (2, False)
    assert key.stats()["resets"] == 1
    assert key.verify_pin(PIN, now=4).status == "ok"


def test_stats_count_each_operation():
    key = store(max_retries=4)
    key.register_credential("cred-a", "alice", now=0)
    key.verify_pin(PIN, now=1)
    key.verify_pin("000000", now=2)
    key.change_pin(PIN, "135790", now=3)
    key.delete_credential("cred-a")
    assert key.stats() == {"verifies": 1, "failures": 1, "hard_locks": 0,
                           "resets": 0, "registered": 1, "deleted": 1,
                           "pin_changes": 1}


def test_snapshot_is_detached_and_restore_roundtrips():
    key = store(max_retries=3)
    key.register_credential("cred-a", "alice", rp_id="rp", now=5)
    key.verify_pin("000000", now=6)
    state = key.snapshot()
    assert set(state) == {"version", "pin", "retries_left", "hard_locked",
                          "credentials", "counters", "device"}
    pristine = copy.deepcopy(state)
    state["credentials"].clear()
    state["counters"]["failures"] = 99
    assert len(key.list_credentials()) == 1
    assert key.stats()["failures"] == 1
    clone = store()
    clone.restore(pristine)
    assert clone.pin_status().retries_left == 2
    assert clone.verify_pin(PIN, now=7).status == "ok"
    assert clone.stats()["failures"] == 1
    assert clone.list_credentials()[0].credential_id == "cred-a"


def test_restore_rejects_malformed_snapshot():
    key = store()
    key.register_credential("cred-a", "alice", now=0)
    device = {"device_id": "key-0001", "firmware_generation": 1}
    bad_states = [
        None,
        {},
        {"version": 2, "pin": PIN, "retries_left": 1, "hard_locked": False,
         "credentials": [], "counters": {}, "device": device},
        {"version": 1, "pin": PIN, "retries_left": 1, "hard_locked": False,
         "credentials": "nope", "counters": {}, "device": device},
        {"version": 1, "pin": PIN, "retries_left": 1, "hard_locked": False,
         "credentials": [], "counters": {}, "device": {}},
    ]
    for bad in bad_states:
        with pytest.raises(fp.Fido2PinError) as exc:
            key.restore(bad)
        assert exc.value.code == "PIN_SNAPSHOT_INVALID"
    assert key.verify_pin(PIN, now=1).status == "ok"
    assert [row.credential_id for row in key.list_credentials()] == ["cred-a"]


def test_concurrent_registration_of_same_id_yields_one_record():
    key = store(max_resident=8)
    outcomes = []
    barrier = threading.Barrier(8)

    def worker(index):
        barrier.wait(timeout=5)
        try:
            key.register_credential("cred-race", f"user-{index}", now=index)
            outcomes.append("created")
        except fp.Fido2PinError as exc:
            outcomes.append(exc.code)
        except Exception as exc:  # pragma: no cover - reported via outcomes
            outcomes.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(index,))
               for index in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert outcomes.count("created") == 1
    assert outcomes.count("CREDENTIAL_DUPLICATE") == 7
    assert len(key.list_credentials()) == 1
    assert key.stats()["registered"] == 1
