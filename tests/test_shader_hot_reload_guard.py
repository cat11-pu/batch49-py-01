import copy

import pytest

import shader_hot_reload_guard as sh


def guard(**kwargs):
    return sh.ShaderHotReloadGuard(lambda path: (True, None), **kwargs)


def test_watch_validates_arguments():
    with pytest.raises(sh.ShaderReloadError) as exc:
        sh.ShaderHotReloadGuard(None)
    assert exc.value.code == "SHADER_ARG_INVALID"
    g = guard()
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.watch("", "a.hlsl")
    assert exc.value.code == "SHADER_ARG_INVALID"
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.watch("prog", "  ")
    assert exc.value.code == "SHADER_ARG_INVALID"
    info = g.watch("prog", "a.hlsl")
    assert (info.program_id, info.path, info.generation) == ("prog", "a.hlsl", 1)
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.watch("prog", "b.hlsl")
    assert exc.value.code == "SHADER_PROGRAM_DUPLICATE"


def test_watch_enforces_dependency_limit():
    g = guard(max_dependencies=4)
    edges = {
        "a.hlsl": ["b.hlsl"],
        "b.hlsl": ["c.hlsl"],
        "c.hlsl": ["d.hlsl"],
        "d.hlsl": ["e.hlsl"],
        "e.hlsl": ["f.hlsl"],
    }
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.watch("prog", "a.hlsl", edges)
    assert exc.value.code == "SHADER_DEPENDENCY_LIMIT"
    assert g.programs() == ()


def test_touch_file_marks_only_dependent_programs():
    g = guard()
    g.watch("main", "main.hlsl", {"main.hlsl": ["common.hlsl"],
                                  "common.hlsl": ["noise.hlsl"]})
    g.watch("ui", "ui.hlsl", {"ui.hlsl": ["theme.hlsl"]})
    assert g.touch_file("common.hlsl") == 1
    assert g.program_state("main") == "dirty"
    assert g.program_state("ui") == "ready"
    assert g.touch_file("missing.hlsl") == 0
    assert g.stats()["touches"] == 2


def test_dependencies_returns_sorted_closure():
    g = guard()
    g.watch("main", "main.hlsl", {"main.hlsl": ["noise.hlsl", "common.hlsl"],
                                  "common.hlsl": ["types.hlsl"]})
    assert g.dependencies("main") == ("common.hlsl", "noise.hlsl",
                                      "types.hlsl")
    assert g.dependencies("main") == tuple(sorted(g.dependencies("main")))


def test_dependencies_rejects_unknown_program():
    g = guard()
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.dependencies("ghost")
    assert exc.value.code == "SHADER_UNKNOWN_PROGRAM"
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.generation("ghost")
    assert exc.value.code == "SHADER_UNKNOWN_PROGRAM"


def test_rebuild_rejects_unknown_and_clean_programs():
    g = guard()
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.rebuild("ghost")
    assert exc.value.code == "SHADER_UNKNOWN_PROGRAM"
    g.watch("main", "main.hlsl")
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.rebuild("main")
    assert exc.value.code == "SHADER_NOT_DIRTY"
    assert g.program_state("main") == "ready"


def test_rebuild_success_stages_without_changing_generation():
    g = guard()
    g.watch("main", "main.hlsl")
    g.touch_file("main.hlsl")
    assert g.program_state("main") == "dirty"
    outcome = g.rebuild("main")
    assert isinstance(outcome, sh.RebuildOutcome)
    assert (outcome.program_id, outcome.ok, outcome.error) == ("main", True, None)
    assert outcome.generation == 1
    assert g.program_state("main") == "staged"
    assert g.generation("main") == 1
    assert g.pending() == ("main",)
    assert g.stats()["rebuilds"] == 1


def test_rebuild_failure_keeps_old_generation_and_records_error():
    g = sh.ShaderHotReloadGuard(lambda path: (False, "syntax error"))
    g.watch("main", "main.hlsl")
    g.touch_file("main.hlsl")
    outcome = g.rebuild("main")
    assert outcome.ok is False
    assert outcome.generation == 1
    assert outcome.error == "syntax error"
    assert g.program_state("main") == "failed"
    assert g.generation("main") == 1
    assert g.errors() == {"main": "syntax error"}
    assert g.pending() == ()
    assert g.stats()["failures"] == 1


def test_commit_switches_staged_programs_and_bumps_generation():
    g = guard()
    g.watch("main", "main.hlsl")
    g.touch_file("main.hlsl")
    g.rebuild("main")
    assert g.commit_at_frame_boundary() == 1
    assert g.generation("main") == 2
    assert g.program_state("main") == "ready"
    assert g.pending() == ()
    assert g.commit_at_frame_boundary() == 0
    assert g.stats()["commits"] == 1


def test_commit_defers_beyond_frame_budget():
    g = guard(max_rebuilds_per_frame=1)
    for program_id in ("alpha", "beta"):
        g.watch(program_id, program_id + ".hlsl")
        g.touch_file(program_id + ".hlsl")
        g.rebuild(program_id)
    assert g.pending() == ("alpha", "beta")
    assert g.commit_at_frame_boundary() == 1
    assert g.generation("alpha") == 2
    assert g.generation("beta") == 1
    assert g.pending() == ("beta",)
    assert g.commit_at_frame_boundary() == 1
    assert g.generation("beta") == 2
    assert g.pending() == ()


def test_rollback_returns_one_generation_and_then_refuses():
    g = guard()
    g.watch("main", "main.hlsl")
    g.touch_file("main.hlsl")
    g.rebuild("main")
    g.commit_at_frame_boundary()
    assert g.rollback("main") == 1
    assert g.generation("main") == 1
    assert g.program_state("main") == "ready"
    assert g.stats()["rollbacks"] == 1
    with pytest.raises(sh.ShaderReloadError) as exc:
        g.rollback("main")
    assert exc.value.code == "SHADER_NO_ROLLBACK"


def test_snapshot_is_detached_and_restore_roundtrips():
    g = guard()
    g.watch("main", "main.hlsl", {"main.hlsl": ["common.hlsl"]})
    g.watch("ui", "ui.hlsl")
    g.touch_file("main.hlsl")
    g.rebuild("main")
    state = g.snapshot()
    assert set(state) == {"version", "programs", "counters"}
    pristine = copy.deepcopy(state)
    state["programs"].clear()
    state["counters"]["commits"] = 99
    assert g.pending() == ("main",)
    assert g.program_state("main") == "staged"
    clone = guard()
    clone.restore(pristine)
    assert clone.pending() == ("main",)
    assert clone.program_state("main") == "staged"
    assert clone.dependencies("main") == ("common.hlsl",)
    assert clone.commit_at_frame_boundary() == 1
    assert clone.generation("main") == 2


def test_restore_rejects_malformed_state():
    g = guard()
    g.watch("main", "main.hlsl")
    g.touch_file("main.hlsl")
    bad_states = [
        None,
        {},
        {"version": 3, "programs": {}, "counters": {}},
        {"version": 1, "programs": [], "counters": {}},
        {"version": 1, "programs": {}, "counters": {"touches": 1}},
    ]
    for bad in bad_states:
        with pytest.raises(sh.ShaderReloadError) as exc:
            g.restore(bad)
        assert exc.value.code == "SHADER_SNAPSHOT_INVALID"
    assert g.program_state("main") == "dirty"
    assert g.touch_file("main.hlsl") == 1


def test_stats_track_each_operation():
    g = guard(max_rebuilds_per_frame=1)
    g.watch("alpha", "a.hlsl")
    g.watch("beta", "b.hlsl")
    g.touch_file("a.hlsl")
    g.touch_file("b.hlsl")
    g.rebuild("alpha")
    g.commit_at_frame_boundary()
    assert g.stats() == {
        "programs": 2,
        "dirty": 1,
        "staged": 0,
        "rebuilds": 1,
        "failures": 0,
        "touches": 2,
        "commits": 1,
        "rollbacks": 0,
    }


def test_touch_marks_every_program_sharing_an_include():
    g = guard()
    g.watch("alpha", "a.hlsl", {"a.hlsl": ["common.hlsl"]})
    g.watch("beta", "b.hlsl", {"b.hlsl": ["common.hlsl"]})
    g.watch("gamma", "c.hlsl")
    assert g.touch_file("common.hlsl") == 2
    assert g.program_state("alpha") == "dirty"
    assert g.program_state("beta") == "dirty"
    assert g.program_state("gamma") == "ready"
    assert g.stats()["dirty"] == 2
