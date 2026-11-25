import pytest

import isolation_lineage_guard as ilg


def guard(**kwargs):
    return ilg.IsolationLineageGuard(**kwargs)


def row(pid, ppid, starttime, state="R", adopted_by=None):
    return ilg.ProcRow(pid=pid, ppid=ppid, starttime=starttime, state=state,
                       adopted_by=adopted_by)


def test_register_records_root_process():
    lin = guard()
    lin.register_unit("u1")
    entry = lin.register("u1", 10, starttime=1)
    assert (entry.pid, entry.starttime, entry.unit_id) == (10, 1, "u1")
    assert entry.depth == 0
    assert entry.parent_pid is None
    assert entry.state == "live"
    assert tuple(lin.lineage("u1")) == (entry,)
    assert lin.owner_of(10, 1) == "u1"
    assert lin.generation("u1") == 1


def test_spawn_extends_the_lineage():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    child = lin.spawn("u1", 10, 11, starttime=2)
    assert child.depth == 1
    assert child.parent_pid == 10
    assert lin.active_children("u1") == 1
    assert [entry.pid for entry in lin.lineage("u1")] == [10, 11]


def test_parent_in_another_unit_is_refused_and_recorded():
    lin = guard()
    lin.register_unit("u1")
    lin.register_unit("u2")
    lin.register("u1", 10, starttime=1)
    lin.register("u2", 20, starttime=2)
    with pytest.raises(ilg.LineageError) as exc:
        lin.spawn("u2", 10, 21, starttime=3)
    assert exc.value.code == "LINEAGE_CROSS_DOMAIN"
    assert exc.value.detail["owner_unit"] == "u1"
    records = lin.cross_domain()
    assert len(records) == 1
    record = records[0]
    assert (record.pid, record.starttime) == (21, 3)
    assert (record.owner_unit, record.target_unit) == ("u1", "u2")
    assert record.reason == "ppid_cross_domain"
    assert [entry.pid for entry in lin.lineage("u2")] == [20]


def test_unknown_parent_is_refused():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    with pytest.raises(ilg.LineageError) as exc:
        lin.spawn("u1", 999, 11, starttime=2)
    assert exc.value.code == "LINEAGE_PARENT_UNKNOWN"
    assert lin.active_children("u1") == 0


def test_pid_reuse_is_split_by_starttime():
    lin = guard()
    lin.register_unit("u1")
    lin.register_unit("u2")
    lin.register("u1", 100, starttime=1)
    lin.register("u2", 100, starttime=2)
    assert lin.owner_of(100, 1) == "u1"
    assert lin.owner_of(100, 2) == "u2"
    assert lin.owner_of(100, 3) is None
    assert len(lin.lineage("u1")) == 1
    assert len(lin.lineage("u2")) == 1


def test_same_process_cannot_be_registered_twice():
    lin = guard()
    lin.register_unit("u1")
    lin.register_unit("u2")
    lin.register("u1", 5, starttime=42)
    with pytest.raises(ilg.LineageError) as exc:
        lin.register("u2", 5, starttime=42)
    assert exc.value.code == "LINEAGE_ALREADY_OWNED"
    assert exc.value.detail["owner_unit"] == "u1"


def test_child_quota_per_unit():
    lin = guard(max_children=2)
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    lin.spawn("u1", 10, 12, starttime=3)
    with pytest.raises(ilg.LineageError) as exc:
        lin.spawn("u1", 10, 13, starttime=4)
    assert exc.value.code == "LINEAGE_QUOTA"
    assert exc.value.detail["limit"] == 2


def test_depth_cap_refuses_deeper_children_and_scan_reports_truncation():
    lin = guard(max_depth=2)
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    lin.spawn("u1", 11, 12, starttime=3)
    with pytest.raises(ilg.LineageError) as exc:
        lin.spawn("u1", 12, 13, starttime=4)
    assert exc.value.code == "LINEAGE_DEPTH_LIMIT"
    result = lin.scan("u1", [row(10, 0, 1), row(11, 10, 2), row(12, 11, 3)])
    assert result.checked == 3
    assert result.truncated == 1
    assert result.missing == ()


def test_scan_tolerates_missing_processes():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 30, starttime=9)
    result = lin.scan("u1", [])
    assert result.missing == (30,)
    assert result.violations == ()
    assert result.escapes == ()
    assert result.exited == ()
    assert lin.lineage("u1")[0].state == "live"


def test_scan_flags_ppid_pointing_into_another_unit():
    lin = guard()
    lin.register_unit("u1")
    lin.register_unit("u2")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    lin.register("u2", 20, starttime=3)
    result = lin.scan("u1", [row(10, 0, 1), row(11, 20, 2)])
    assert len(result.violations) == 1
    hit = result.violations[0]
    assert (hit.pid, hit.owner_unit, hit.target_unit) == (11, "u2", "u1")
    assert hit.reason == "ppid_cross_domain"


def test_scan_spots_double_fork_adoption():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    result = lin.scan("u1", [row(10, 0, 1), row(11, 777, 2, adopted_by=777)])
    assert result.escapes == (11,)
    assert result.violations == ()
    assert tuple(lin.escape_candidates("u1")) == (11,)


def test_retire_keeps_entries_queryable():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    assert lin.retire("u1") == 2
    assert tuple(lin.lineage("u1")) == ()
    retired = lin.lineage("u1", include_retired=True)
    assert [entry.pid for entry in retired] == [10, 11]
    assert all(entry.state == "retired" for entry in retired)


def test_rebuild_starts_a_new_generation_without_old_records():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    assert lin.rebuild("u1", generation=2) == 2
    assert lin.generation("u1") == 2
    assert tuple(lin.lineage("u1")) == ()
    assert lin.owner_of(10, 1) is None
    fresh = lin.register("u1", 10, starttime=5)
    assert fresh.depth == 0


def test_snapshot_and_restore_keep_lineage_and_rejections():
    lin = guard()
    lin.register_unit("u1")
    lin.register_unit("u2")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    lin.register("u2", 20, starttime=3)
    with pytest.raises(ilg.LineageError):
        lin.spawn("u2", 10, 21, starttime=4)
    state = lin.snapshot()

    revived = guard()
    revived.restore(state)
    assert [entry.pid for entry in revived.lineage("u1")] == [10, 11]
    assert revived.generation("u2") == 1
    assert len(revived.cross_domain()) == 1
    assert revived.cross_domain()[0].target_unit == "u2"
    with pytest.raises(ilg.LineageError) as exc:
        revived.restore({"units": 5})
    assert exc.value.code == "LINEAGE_SNAPSHOT_INVALID"
    assert len(revived.cross_domain()) == 1


def test_zombie_children_stop_counting_against_the_quota():
    lin = guard()
    lin.register_unit("u1")
    lin.register("u1", 10, starttime=1)
    lin.spawn("u1", 10, 11, starttime=2)
    result = lin.scan("u1", [row(10, 0, 1), row(11, 10, 2, state="Z")])
    assert result.exited == (11,)
    assert lin.active_children("u1") == 0
    states = {entry.pid: entry.state for entry in lin.lineage("u1")}
    assert states == {10: "live", 11: "exited"}
