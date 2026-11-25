import copy
import threading

import pytest

import mixer_snapshot_recaller as ms


def rec(**kwargs):
    return ms.MixerSnapshotRecaller(**kwargs)


def test_default_values_and_empty_state():
    r = rec()
    values = r.current_values()
    assert values == {"fader": 0.8, "pan": 0.0, "send": 0.0,
                      "mute": False, "bypass": False}
    assert list(values) == ["fader", "pan", "send", "mute", "bypass"]
    assert r.transition_state() == {"active": False, "elapsed": 0,
                                    "duration": 0, "name": None}
    assert r.stats() == {"captures": 0, "recalls": 0, "applied": 0,
                         "undos": 0, "rejected": 0}


def test_set_param_reports_entry_and_rejects_unknown_name():
    r = rec()
    entry = r.set_param("fader", 0.5)
    assert entry == {"name": "fader", "value": 0.5, "kind": "numeric"}
    assert r.current_values()["fader"] == pytest.approx(0.5)

    with pytest.raises(ms.RecallError) as exc:
        r.set_param("trim", 0.5)
    assert exc.value.code == "RECALL_PARAM_UNKNOWN"
    assert r.stats()["rejected"] == 1


def test_set_param_type_and_range_validation():
    r = rec()
    bad_calls = [
        ("fader", 1.5, "RECALL_RANGE_INVALID"),
        ("send", -0.01, "RECALL_RANGE_INVALID"),
        ("pan", 2.0, "RECALL_RANGE_INVALID"),
        ("fader", True, "RECALL_TYPE_INVALID"),
        ("send", "0.5", "RECALL_TYPE_INVALID"),
        ("mute", 1, "RECALL_TYPE_INVALID"),
        ("bypass", "on", "RECALL_TYPE_INVALID"),
    ]
    for name, value, code in bad_calls:
        with pytest.raises(ms.RecallError) as exc:
            r.set_param(name, value)
        assert exc.value.code == code
    assert r.stats()["rejected"] == len(bad_calls)


def test_capture_stores_detached_values_and_bumps_revision():
    r = rec()
    r.set_param("fader", 0.5)
    first = r.capture("one")
    assert first["name"] == "one"
    assert first["revision"] == 1
    assert first["values"]["fader"] == pytest.approx(0.5)

    r.set_param("fader", 0.2)
    assert first["values"]["fader"] == pytest.approx(0.5)

    second = r.capture("two")
    assert second["revision"] == 2

    first["values"]["fader"] = 9.0
    r.set_param("fader", 0.7)
    r.recall("one")
    assert r.current_values()["fader"] == pytest.approx(0.5)
    assert r.stats()["captures"] == 2


def test_capture_capacity_and_name_validation():
    r = rec(max_snapshots=2)
    r.capture("a")
    r.capture("b")
    with pytest.raises(ms.RecallError) as full:
        r.capture("c")
    assert full.value.code == "RECALL_CAPACITY_EXCEEDED"

    with pytest.raises(ms.RecallError) as empty:
        r.capture("")
    assert empty.value.code == "RECALL_ARGUMENT_INVALID"

    with pytest.raises(ms.RecallError) as not_text:
        r.capture(17)
    assert not_text.value.code == "RECALL_ARGUMENT_INVALID"


def test_recall_without_transition_applies_everything():
    r = rec()
    r.set_param("fader", 0.3)
    r.set_param("pan", -0.4)
    r.set_param("send", 0.6)
    r.set_param("mute", True)
    r.set_param("bypass", True)
    r.capture("state-a")

    r.set_param("fader", 0.9)
    r.set_param("pan", 0.9)
    r.set_param("send", 0.0)
    r.set_param("mute", False)
    r.set_param("bypass", False)

    out = r.recall("state-a")
    assert out["status"] == "applied"
    assert out["duration"] == 0
    assert r.current_values() == {"fader": 0.3, "pan": -0.4, "send": 0.6,
                                  "mute": True, "bypass": True}
    assert r.transition_state()["active"] is False
    assert r.stats()["applied"] == 1


def test_recall_interpolates_numeric_params_until_target():
    r = rec()
    r.set_param("fader", 1.0)
    r.set_param("pan", 1.0)
    r.capture("full")
    r.set_param("fader", 0.0)
    r.set_param("pan", 0.0)

    out = r.recall("full", transition_ms=100)
    assert out["status"] == "running"
    assert out["duration"] == 100

    first = r.interpolate(25)
    assert first["progress"] == pytest.approx(0.25)
    assert first["values"]["fader"] == pytest.approx(0.25)
    assert first["values"]["pan"] == pytest.approx(0.25)

    second = r.interpolate(25)
    assert second["progress"] == pytest.approx(0.5)
    assert second["values"]["fader"] == pytest.approx(0.5)

    last = r.interpolate(50)
    assert last["progress"] == pytest.approx(1.0)
    assert last["values"]["fader"] == pytest.approx(1.0)
    assert last["values"]["pan"] == pytest.approx(1.0)
    assert last["active"] is False
    assert r.transition_state()["active"] is False


def test_interpolate_clamps_past_the_end():
    r = rec()
    r.set_param("send", 1.0)
    r.capture("wet")
    r.set_param("send", 0.0)
    r.recall("wet", transition_ms=80)

    r.interpolate(0)
    out = r.interpolate(500)
    assert out["progress"] == pytest.approx(1.0)
    assert out["active"] is False
    assert out["values"]["send"] == pytest.approx(1.0)

    again = r.interpolate(10)
    assert again["progress"] == pytest.approx(1.0)
    assert again["active"] is False
    assert again["values"]["send"] == pytest.approx(1.0)


def test_recall_rejects_missing_snapshot_and_bad_duration():
    r = rec(max_transition_ms=1000)
    with pytest.raises(ms.RecallError) as missing:
        r.recall("nope")
    assert missing.value.code == "RECALL_SNAPSHOT_MISSING"

    r.capture("here")
    for bad in (-1, 1001, "50", 1.5):
        with pytest.raises(ms.RecallError) as exc:
            r.recall("here", transition_ms=bad)
        assert exc.value.code == "RECALL_TRANSITION_INVALID"


def test_manual_change_during_transition_wins_for_that_param():
    r = rec()
    r.set_param("fader", 1.0)
    r.set_param("pan", 1.0)
    r.capture("target")
    r.set_param("fader", 0.0)
    r.set_param("pan", 0.0)
    r.recall("target", transition_ms=100)

    mid = r.interpolate(50)
    assert mid["values"]["fader"] == pytest.approx(0.5)
    assert mid["values"]["pan"] == pytest.approx(0.5)

    r.set_param("fader", 0.2)
    done = r.interpolate(50)
    assert done["values"]["fader"] == pytest.approx(0.2)
    assert done["values"]["pan"] == pytest.approx(1.0)


def test_excluded_params_follow_the_live_value():
    r = rec()
    r.set_param("fader", 0.2)
    r.set_param("send", 0.9)
    r.capture("loud")
    r.set_param("fader", 0.8)
    r.set_param("send", 0.1)

    assert r.set_exclude(["send"]) == ["send"]
    r.recall("loud")
    values = r.current_values()
    assert values["send"] == pytest.approx(0.1)
    assert values["fader"] == pytest.approx(0.2)

    with pytest.raises(ms.RecallError) as exc:
        r.set_exclude(["send", "gain"])
    assert exc.value.code == "RECALL_PARAM_UNKNOWN"


def test_compare_reports_sorted_differences():
    r = rec()
    r.set_param("fader", 0.2)
    r.set_param("mute", True)
    r.capture("a")
    r.set_param("fader", 0.7)
    r.set_param("mute", False)
    r.capture("b")

    diff = r.compare("a", "b")
    assert [item["param"] for item in diff] == ["fader", "mute"]
    assert diff[0]["from"] == pytest.approx(0.2)
    assert diff[0]["to"] == pytest.approx(0.7)
    assert diff[1]["from"] is True
    assert diff[1]["to"] is False
    assert r.compare("a", "a") == []


def test_undo_recall_restores_previous_values():
    r = rec()
    r.set_param("fader", 0.9)
    r.capture("loud")
    r.set_param("fader", 0.1)

    r.recall("loud")
    assert r.current_values()["fader"] == pytest.approx(0.9)

    out = r.undo_recall()
    assert out["status"] == "undone"
    assert r.current_values()["fader"] == pytest.approx(0.1)
    assert r.stats()["undos"] == 1

    with pytest.raises(ms.RecallError) as exc:
        r.undo_recall()
    assert exc.value.code == "RECALL_NOTHING_TO_UNDO"


def test_snapshot_is_detached_and_restore_roundtrips():
    r = rec()
    r.set_param("fader", 0.4)
    r.capture("stored")
    r.set_exclude(["bypass"])

    state = r.snapshot()
    pristine = copy.deepcopy(state)
    r.set_param("fader", 0.9)

    clone = rec()
    clone.restore(pristine)
    assert clone.current_values()["fader"] == pytest.approx(0.4)
    clone.set_param("fader", 0.0)
    clone.recall("stored")
    assert clone.current_values()["fader"] == pytest.approx(0.4)

    for bad in (None, {}, [], "state"):
        with pytest.raises(ms.RecallError) as exc:
            clone.restore(bad)
        assert exc.value.code == "RECALL_SNAPSHOT_INVALID"
    assert clone.current_values()["fader"] == pytest.approx(0.4)


def test_concurrent_updates_on_distinct_params():
    r = rec()
    targets = {"fader": 0.25, "pan": -0.5, "send": 0.75}
    errors = []
    barrier = threading.Barrier(len(targets))

    def worker(name, value):
        try:
            barrier.wait(timeout=5)
            r.set_param(name, value)
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(name, value))
               for name, value in targets.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    values = r.current_values()
    assert values["fader"] == pytest.approx(0.25)
    assert values["pan"] == pytest.approx(-0.5)
    assert values["send"] == pytest.approx(0.75)
    assert values["mute"] is False
