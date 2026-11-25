import copy

import pytest

import syscall_filter_tripwire as st


def event(event_id, *, pid=100, start=1, profile="shell",
          syscall="openat", ts=1.0):
    return {
        "event_id": event_id,
        "pid": pid,
        "start_time": start,
        "profile": profile,
        "syscall": syscall,
        "ts": ts,
    }


def tripwire(**kwargs):
    return st.SyscallFilterTripwire(**kwargs)


def test_record_accepts_and_reports_count():
    table = tripwire(thresholds={1: 5, 2: 10, 3: 20})
    out = table.record(event("e1"))
    assert out["accepted"] is True
    assert out["duplicate"] is False
    assert out["level"] == 0
    assert out["count"] == 1
    assert table.counts(pid=100, start_time=1)["events"] == 1
    assert table.stats()["events"] == 1


def test_duplicate_event_id_ignored():
    table = tripwire(thresholds={1: 5, 2: 10, 3: 20})
    table.record(event("e1"))
    out = table.record(event("e1", syscall="execve"))
    assert out["accepted"] is False
    assert out["duplicate"] is True
    assert table.counts(pid=100, start_time=1)["events"] == 1
    assert table.counts(pid=100, start_time=1)["by_syscall"] == {"openat": 1}


def test_pid_reuse_separated_by_start_time():
    table = tripwire(thresholds={1: 2, 2: 10, 3: 20})
    table.record(event("a", pid=7, start=1))
    table.record(event("b", pid=7, start=1))
    table.record(event("c", pid=7, start=2))
    assert table.level(pid=7, start_time=1) == 1
    assert table.level(pid=7, start_time=2) == 0
    assert table.counts(pid=7, start_time=1)["events"] == 2
    assert table.counts(pid=7, start_time=2)["events"] == 1


def test_window_expiry_drops_old_events():
    table = tripwire(window=5.0, event_capacity=8, thresholds={1: 9, 2: 9, 3: 9})
    table.record(event("a", ts=10.0))
    table.record(event("b", ts=11.0))
    out = table.record(event("c", ts=20.0))
    assert out["count"] == 1
    assert table.counts(pid=100, start_time=1)["events"] == 1
    assert table.export()["window_remaining"] == 7


def test_escalation_moves_one_level_per_trigger():
    table = tripwire(thresholds={1: 1, 2: 1, 3: 1})
    table.record(event("a", ts=1.0))
    assert table.level(profile="shell") == 1
    table.record(event("b", ts=2.0))
    assert table.level(profile="shell") == 2
    table.record(event("c", ts=3.0))
    assert table.level(profile="shell") == 3


def test_escalation_never_downgrades():
    table = tripwire(window=5.0, thresholds={1: 2, 2: 99, 3: 99})
    table.record(event("a", ts=1.0))
    table.record(event("b", ts=2.0))
    assert table.level(profile="shell") == 1
    table.record(event("c", ts=100.0))
    assert table.level(profile="shell") == 1
    assert table.counts(pid=100, start_time=1)["events"] == 1


def test_profile_escalation_pauses_new_admissions():
    table = tripwire(window=100.0, thresholds={1: 1, 2: 1, 3: 1})
    table.record(event("a", pid=100, ts=1.0))
    assert table.admit(pid=100, start_time=1, profile="shell") is True
    table.record(event("b", pid=101, ts=2.0))
    assert table.admit(pid=101, start_time=1, profile="shell") is False
    assert table.admit(pid=100, start_time=1, profile="shell") is True
    assert table.stats()["denied"] == 1


def test_paused_profile_keeps_recording_existing_violations():
    table = tripwire(window=100.0, thresholds={1: 1, 2: 1, 3: 99})
    table.record(event("a", pid=100, ts=1.0))
    assert table.admit(pid=100, start_time=1, profile="shell") is True
    table.record(event("b", pid=200, ts=2.0))
    out = table.record(event("c", pid=100, ts=3.0))
    assert out["accepted"] is True
    assert table.counts(pid=100, start_time=1)["events"] == 2
    assert table.admit(pid=100, start_time=1, profile="shell") is True


def test_reset_requires_operator_and_reason():
    table = tripwire(thresholds={1: 5, 2: 10, 3: 20})
    with pytest.raises(st.TripwireError) as exc:
        table.reset(operator="", reason="drill")
    assert exc.value.code == "RESET_INVALID"
    with pytest.raises(st.TripwireError) as exc2:
        table.reset(operator="alice", reason="   ")
    assert exc2.value.code == "RESET_INVALID"
    assert table.stats()["resets"] == 0


def test_reset_clears_scope_counters_and_level():
    table = tripwire(thresholds={1: 2, 2: 99, 3: 99})
    table.record(event("a", pid=100, profile="shell", ts=1.0))
    table.record(event("b", pid=100, profile="shell", ts=2.0))
    table.record(event("c", pid=300, profile="db", ts=3.0))
    assert table.level(profile="shell") == 1
    out = table.reset(operator="alice", reason="drill", profile="shell")
    assert out["level"] == 0
    assert table.level(profile="shell") == 0
    assert table.counts(pid=100, start_time=1)["events"] == 0
    assert table.counts(pid=300, start_time=1)["events"] == 1
    assert table.stats()["resets"] == 1


def test_threshold_change_affects_next_trigger():
    table = tripwire(thresholds={1: 5, 2: 99, 3: 99})
    table.set_threshold(1, 3)
    assert table.threshold(1) == 3
    table.record(event("a", ts=1.0))
    table.record(event("b", ts=2.0))
    assert table.level(profile="shell") == 0
    table.record(event("c", ts=3.0))
    assert table.level(profile="shell") == 1


def test_export_has_required_sections():
    table = tripwire(event_capacity=32, thresholds={1: 1, 2: 99, 3: 99})
    table.record(event("a", ts=1.0, syscall="openat"))
    table.record(event("b", ts=2.0, syscall="execve"))
    table.reset(operator="op", reason="test", profile="shell")
    out = table.export()
    assert {"profiles", "levels", "process_levels", "events_by_level",
            "escalations", "resets", "window_remaining", "dropped"} <= set(out)
    assert out["resets"] == 1
    assert out["window_remaining"] == 32
    assert out["profiles"] == ["shell"]


def test_process_buffer_drops_oldest():
    table = tripwire(process_capacity=4, thresholds={1: 99, 2: 99, 3: 99})
    for index in range(6):
        table.record(event(f"e{index}", ts=float(index)))
    assert table.counts(pid=100, start_time=1)["events"] == 4
    assert table.stats()["dropped"] == 2


def test_profile_capacity_rejects_new_profile():
    table = tripwire(profile_capacity=2, thresholds={1: 99, 2: 99, 3: 99})
    table.record(event("a", profile="p1"))
    table.record(event("b", profile="p2"))
    with pytest.raises(st.TripwireError) as exc:
        table.record(event("c", profile="p3"))
    assert exc.value.code == "PROFILE_CAPACITY"
    assert table.stats()["rejected"] == 1
    assert "p3" not in table.export()["profiles"]


def test_persist_and_load_keep_level_and_window(tmp_path):
    table = tripwire(thresholds={1: 1, 2: 99, 3: 99})
    table.record(event("a", ts=1.0))
    table.record(event("b", ts=2.0))
    path = tmp_path / "tripwire.json"
    assert table.persist(str(path)) == 1

    fresh = tripwire()
    fresh.load(str(path))
    assert fresh.level(profile="shell") == 1
    assert fresh.export()["profiles"] == ["shell"]
    state = copy.deepcopy(fresh.snapshot())
    assert {"levels", "counters", "stats"} <= set(state)
    with pytest.raises(st.TripwireError) as exc:
        fresh.restore({})
    assert exc.value.code == "STATE_INVALID"
    assert fresh.level(profile="shell") == 1
