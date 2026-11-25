import pytest

import isolation_unit_attestor as iua


def spec(**kwargs):
    base = dict(unit_id="u1", uid=1001, gid=1001,
                tmpdir="/run/units/u1/tmp", unit_root="/run/units/u1",
                writable_mounts=("/run/units/u1/tmp",),
                read_only_mounts=("/usr", "/etc"),
                cgroup_path="/sys/fs/cgroup/units/u1")
    base.update(kwargs)
    return iua.UnitSpec(**base)


def own_process(declared, pid=10, starttime=555):
    return iua.ProcessInfo(pid=pid, uid=declared.uid, starttime=starttime,
                           unit_id=declared.unit_id,
                           cgroup_path=declared.cgroup_path)


def intruder(declared, pid=77, starttime=9001, unit_id="u2"):
    return iua.ProcessInfo(pid=pid, uid=declared.uid, starttime=starttime,
                           unit_id=unit_id,
                           cgroup_path="/sys/fs/cgroup/units/" + unit_id)


def tmpstat(declared, **kwargs):
    params = dict(uid=declared.uid, gid=declared.gid, mode=0o700)
    params.update(kwargs)
    return iua.DirStat(**params)


def test_clean_unit_starts_with_all_items_ok():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    report = attestor.start(declared, processes=(own_process(declared),),
                            tmpdir_stat=tmpstat(declared), now=100)
    assert report.status == "clean"
    assert [item.name for item in report.items] == [
        "uid_dedicated", "supplementary_groups", "tmpdir",
        "cgroup_membership", "mounts", "sources"]
    assert all(item.ok for item in report.items)
    assert report.last_checked_at == 100
    assert report.taint_reason is None
    assert attestor.status("u1") == "clean"
    assert attestor.accepting("u1") is True
    assert attestor.draining("u1") is False
    assert attestor.unit_count() == 1


def test_shared_uid_refuses_start_and_names_the_process():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),
                                            intruder(declared, pid=77,
                                                     starttime=9001)),
                       tmpdir_stat=tmpstat(declared), now=100)
    assert exc.value.code == "ISOLATION_UID_SHARED"
    assert exc.value.detail["pid"] == 77
    assert exc.value.detail["starttime"] == 9001
    assert attestor.status("u1") == "absent"


def test_supplementary_group_refuses_but_primary_group_is_recorded():
    declared = spec(supplementary_groups=(27, 44))
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared), now=1)
    assert exc.value.code == "ISOLATION_SUPPLEMENTARY_GROUP"
    assert tuple(exc.value.detail["supplementary"]) == (27, 44)

    plain = spec()
    report = attestor.start(plain, processes=(own_process(plain),),
                            tmpdir_stat=tmpstat(plain), now=2)
    item = {entry.name: entry for entry in report.items}["supplementary_groups"]
    assert item.ok is True
    assert item.detail["primary_gid"] == plain.gid


def test_tmpdir_owned_by_another_user_is_refused():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared, uid=999), now=1)
    assert exc.value.code == "ISOLATION_TMPDIR_UNSAFE"
    assert exc.value.detail["uid"] == 999


def test_tmpdir_group_or_other_permissions_are_refused():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared, mode=0o755), now=1)
    assert exc.value.code == "ISOLATION_TMPDIR_UNSAFE"
    assert exc.value.detail["mode"] == 0o755


def test_tmpdir_symlink_is_refused_only_when_it_points_outside():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared, symlink_target="/var/tmp"),
                       now=1)
    assert exc.value.code == "ISOLATION_TMPDIR_UNSAFE"
    assert exc.value.detail["symlink_target"] == "/var/tmp"

    inside = tmpstat(declared, symlink_target="/run/units/u1/target")
    report = attestor.start(declared, processes=(own_process(declared),),
                            tmpdir_stat=inside, now=2)
    assert report.status == "clean"


def test_cgroup_membership_compares_path_segments():
    attestor = iua.IsolationUnitAttestor()
    assert attestor.match_cgroup("/sys/fs/cgroup/units/u1",
                                 "/sys/fs/cgroup/units/u1") is True
    assert attestor.match_cgroup("/sys/fs/cgroup/units/u1",
                                 "/sys/fs/cgroup/units/u1/worker") is True
    assert attestor.match_cgroup("/sys/fs/cgroup/units/u1",
                                 "/sys/fs/cgroup/units/u10") is False
    assert attestor.match_cgroup("/sys/fs/cgroup/units/u1",
                                 "/sys/fs/cgroup/units/u1x") is False
    assert attestor.match_cgroup("/sys/fs/cgroup/units/u1",
                                 "/sys/fs/cgroup/other/u1") is False


def test_writable_mount_outside_unit_root_is_refused():
    declared = spec(writable_mounts=("/run/units/u1/tmp", "/var/tmp"))
    attestor = iua.IsolationUnitAttestor()
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared), now=1)
    assert exc.value.code == "ISOLATION_MOUNT_ESCAPE"
    assert tuple(exc.value.detail["escaped"]) == ("/var/tmp",)


def test_unreadable_check_source_counts_as_failure():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    items = attestor.verify(declared, processes=(own_process(declared),),
                            tmpdir_stat=tmpstat(declared), unreadable=("proc",))
    sources = {item.name: item for item in items}["sources"]
    assert sources.ok is False
    assert tuple(sources.detail["unreadable"]) == ("proc",)
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(declared, processes=(own_process(declared),),
                       tmpdir_stat=tmpstat(declared), unreadable=("proc",),
                       now=1)
    assert exc.value.code == "ISOLATION_READ_FAILED"


def test_recheck_marks_unit_tainted_with_conflicting_process():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    attestor.start(declared, processes=(own_process(declared),),
                   tmpdir_stat=tmpstat(declared), now=100)
    report = attestor.recheck("u1", processes=(own_process(declared),
                                               intruder(declared, pid=91,
                                                        starttime=4444)),
                              now=200)
    assert report.status == "tainted"
    assert report.taint_reason == "uid_shared"
    assert tuple(report.conflicts) == ((91, 4444),)
    assert report.last_checked_at == 200
    assert attestor.status("u1") == "tainted"
    assert attestor.accepting("u1") is False
    assert attestor.draining("u1") is True


def test_reset_needs_a_reason_and_records_it():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    attestor.start(declared, processes=(own_process(declared),),
                   tmpdir_stat=tmpstat(declared), now=100)
    attestor.recheck("u1", processes=(own_process(declared),
                                      intruder(declared, pid=91, starttime=4444)),
                     now=200)
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.reset("u1", "   ", now=300)
    assert exc.value.code == "ISOLATION_RESET_REASON"
    assert attestor.status("u1") == "tainted"

    attestor.reset("u1", "audited by hand", now=301)
    assert attestor.status("u1") == "clean"
    assert attestor.accepting("u1") is True
    assert attestor.draining("u1") is False
    assert attestor.snapshot()["resets"][-1]["unit"] == "u1"
    assert attestor.snapshot()["resets"][-1]["reason"] == "audited by hand"
    assert attestor.snapshot()["resets"][-1]["at"] == 301


def test_two_rechecks_of_one_unit_do_not_overlap():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    attestor.start(declared, processes=(own_process(declared),),
                   tmpdir_stat=tmpstat(declared), now=100)
    attestor.begin_recheck("u1")
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.recheck("u1", processes=(own_process(declared),), now=200)
    assert exc.value.code == "ISOLATION_RECHECK_BUSY"
    attestor.end_recheck("u1")
    report = attestor.recheck("u1", processes=(own_process(declared),), now=201)
    assert report.status == "clean"


def test_snapshot_keeps_taint_and_last_check_across_restore():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    attestor.start(declared, processes=(own_process(declared),),
                   tmpdir_stat=tmpstat(declared), now=100)
    attestor.taint("u1", "uid_shared", conflicts=((91, 4444),), now=300)
    state = attestor.snapshot()
    assert state["units"]["u1"]["last_checked_at"] == 300

    revived = iua.IsolationUnitAttestor()
    revived.restore(state)
    assert revived.status("u1") == "tainted"
    assert revived.accepting("u1") is False
    assert revived.snapshot()["units"]["u1"]["taint_reason"] == "uid_shared"
    with pytest.raises(iua.IsolationAttestError) as exc:
        revived.restore({"units": []})
    assert exc.value.code == "ISOLATION_SNAPSHOT_INVALID"
    assert revived.status("u1") == "tainted"


def test_unit_count_is_capped():
    first = spec()
    second = spec(unit_id="u2", uid=1002, gid=1002,
                  tmpdir="/run/units/u2/tmp", unit_root="/run/units/u2",
                  writable_mounts=("/run/units/u2/tmp",),
                  cgroup_path="/sys/fs/cgroup/units/u2")
    attestor = iua.IsolationUnitAttestor(max_units=1)
    attestor.start(first, processes=(own_process(first),),
                   tmpdir_stat=tmpstat(first), now=1)
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.start(second, processes=(own_process(second),),
                       tmpdir_stat=tmpstat(second), now=2)
    assert exc.value.code == "ISOLATION_UNIT_LIMIT"
    assert attestor.unit_count() == 1


def test_tainted_unit_rejects_new_tasks_but_keeps_running_ones():
    declared = spec()
    attestor = iua.IsolationUnitAttestor()
    attestor.start(declared, processes=(own_process(declared),),
                   tmpdir_stat=tmpstat(declared), now=100)
    assert attestor.submit("u1", "task-1") == "queued"
    attestor.recheck("u1", processes=(own_process(declared),
                                      intruder(declared, pid=91, starttime=4444)),
                     now=200)
    with pytest.raises(iua.IsolationAttestError) as exc:
        attestor.submit("u1", "task-2")
    assert exc.value.code == "ISOLATION_TAINTED"
    assert tuple(attestor.tasks("u1")) == ("task-1",)
