import pytest

import patch_precondition_planner as pp

EPOLL = "net/epoll.c"


def asset_planner(**kwargs):
    inst = pp.PatchPreconditionPlanner(**kwargs)
    inst.add_asset("web", "2.0")
    return inst


def test_base_version_gate_matches_asset_version():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-100", files=("a.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-101", files=("b.c",), base_version="1.9"))
    assert inst.asset_version("web") == "2.0"
    assert inst.base_version_ok("web", "p-100") is True
    assert inst.base_version_ok("web", "p-101") is False


def test_add_patch_rejects_duplicate_and_invalid_ids():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="2.0"))
    with pytest.raises(pp.PatchPlanError) as exc:
        inst.add_patch(pp.Patch("p-1", files=("b.c",), base_version="2.0"))
    assert exc.value.code == "PATCH_DUPLICATE"
    with pytest.raises(pp.PatchPlanError) as exc:
        inst.add_patch(pp.Patch("   ", files=("a.c",), base_version="2.0"))
    assert exc.value.code == "PATCH_INVALID"
    with pytest.raises(pp.PatchPlanError) as exc:
        inst.add_patch(pp.Patch("p-2", files=(), base_version="2.0"))
    assert exc.value.code == "PATCH_INVALID"
    assert inst.stats()["patches"] == 1


def test_conflicts_reports_shared_file():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 10, 40),)))
    inst.add_patch(pp.Patch("p-2", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 30, 60),)))
    assert inst.conflicts("p-1", "p-2") == (EPOLL,)
    assert inst.conflicts("p-2", "p-1") == (EPOLL,)


def test_conflicts_empty_when_files_differ():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-3", files=("mm/slab.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-4", files=("fs/namei.c",), base_version="2.0"))
    assert inst.conflicts("p-3", "p-4") == ()
    assert inst.conflicts("p-4", "p-3") == ()


def test_prerequisite_lands_in_earlier_batch():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="2.0",
                            requires=("p-1",)))
    result = inst.plan("web", ["p-2", "p-1"])
    assert result.asset_id == "web"
    assert result.batches == (("p-1",), ("p-2",))
    assert result.blocked == ()


def test_independent_patches_share_one_batch():
    inst = asset_planner()
    for index in (3, 1, 2):
        inst.add_patch(pp.Patch(f"p-{index}", files=(f"f{index}.c",),
                                base_version="2.0"))
    result = inst.plan("web", ["p-3", "p-1", "p-2"])
    assert result.batches == (("p-1", "p-2", "p-3"),)
    assert result.blocked == ()
    assert inst.stats()["plans"] == 1


def test_same_file_patches_split_across_batches():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 10, 40),)))
    inst.add_patch(pp.Patch("p-2", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 30, 60),)))
    result = inst.plan("web", ["p-1", "p-2"])
    assert result.batches == (("p-1",), ("p-2",))
    assert result.blocked == ()


def test_base_version_mismatch_is_blocked_not_scheduled():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="1.8"))
    result = inst.plan("web", ["p-1"])
    assert result.batches == ()
    assert result.blocked == ("p-1",)
    assert ("p-1", "BASE_VERSION_MISMATCH") in result.reasons


def test_dependent_of_blocked_patch_is_blocked():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="1.8"))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="2.0",
                            requires=("p-1",)))
    result = inst.plan("web", ["p-2", "p-1"])
    assert result.batches == ()
    assert result.blocked == ("p-1", "p-2")
    assert ("p-2", "PRECONDITION_BLOCKED") in result.reasons
    assert inst.stats()["blocked"] == 2


def test_missing_prerequisite_raises():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-9", files=("a.c",), base_version="2.0",
                            requires=("p-404",)))
    with pytest.raises(pp.PatchPlanError) as exc:
        inst.plan("web", ["p-9"])
    assert exc.value.code == "PATCH_PRECONDITION_MISSING"
    assert exc.value.detail == "p-404"


def test_precondition_cycle_raises():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="2.0",
                            requires=("p-2",)))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="2.0",
                            requires=("p-1",)))
    with pytest.raises(pp.PatchPlanError) as exc:
        inst.plan("web", ["p-1", "p-2"])
    assert exc.value.code == "PATCH_CYCLE"
    assert inst.stats()["plans"] == 0


def test_max_batch_size_caps_each_batch():
    inst = asset_planner(max_batch_size=2)
    for index in range(1, 6):
        inst.add_patch(pp.Patch(f"p-{index}", files=(f"f{index}.c",),
                                base_version="2.0"))
    result = inst.plan("web", [f"p-{index}" for index in range(1, 6)])
    assert result.batches == (("p-1", "p-2"), ("p-3", "p-4"), ("p-5",))


def test_minimum_blocking_set_lists_root_causes():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="1.0"))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="1.5"))
    inst.add_patch(pp.Patch("p-3", files=("c.c",), base_version="2.0"))
    blocked = inst.minimum_blocking_set("web", ["p-2", "p-1", "p-3"])
    assert blocked == ("p-1", "p-2")


def test_snapshot_restore_roundtrip():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="1.0"))
    state = inst.snapshot()
    clone = pp.PatchPreconditionPlanner()
    clone.restore(state)
    assert clone.asset_version("web") == "2.0"
    assert clone.base_version_ok("web", "p-1") is True
    assert clone.base_version_ok("web", "p-2") is False
    assert clone.plan("web", ["p-1", "p-2"]) == inst.plan("web", ["p-1", "p-2"])


def test_plan_is_independent_of_input_order():
    inst = asset_planner()
    inst.add_patch(pp.Patch("p-1", files=("a.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-2", files=("b.c",), base_version="2.0",
                            requires=("p-1",)))
    inst.add_patch(pp.Patch("p-3", files=("c.c",), base_version="2.0"))
    inst.add_patch(pp.Patch("p-4", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 10, 40),)))
    inst.add_patch(pp.Patch("p-5", files=(EPOLL,), base_version="2.0",
                            hunks=((EPOLL, 30, 60),)))
    first = inst.plan("web", ["p-5", "p-2", "p-4", "p-1", "p-3"])
    second = inst.plan("web", ["p-1", "p-3", "p-2", "p-5", "p-4"])
    assert first == second
    assert first.batches == (("p-1", "p-3", "p-4"), ("p-2", "p-5"))
