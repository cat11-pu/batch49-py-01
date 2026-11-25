import pytest

import acl_template_rollout as atr

UID_MAP = {"alice": 1001, "bob": 1002, "carol": 1003}
GID_MAP = {"ops": 2001, "dev": 2002}


def entry(tag, qualifier, perms, default=False, line=0):
    return atr.TemplateEntry(tag=tag, qualifier=qualifier, perms=perms,
                             default=default, line=line)


def rollout(store=None, **kwargs):
    kwargs.setdefault("uid_map", UID_MAP)
    kwargs.setdefault("gid_map", GID_MAP)
    return atr.AclTemplateRollout(store, **kwargs)


def add(roll, path, *, inode, is_dir=True, access=(), default=(), dev=8):
    return roll.add_path(path, dev=dev, inode=inode, is_dir=is_dir,
                         access=access, default=default)


def test_effective_template_uses_most_specific_prefix():
    roll = rollout()
    roll.add_template("/srv/data", [entry("user", "alice", "r")])
    roll.add_template("/srv/data/secret", [entry("user", "bob", "r")])
    deep = roll.effective_template("/srv/data/secret/keys")
    assert [e.qualifier for e in deep if e.tag == "user"] == ["1002"]
    shallow = roll.effective_template("/srv/data/report.csv")
    assert [e.qualifier for e in shallow if e.tag == "user"] == ["1001"]
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.effective_template("/opt/other")
    assert exc.value.code == "ACL_NO_TEMPLATE"


def test_default_entries_on_regular_file_reject_the_batch():
    roll = rollout()
    roll.add_template("/srv/db", [
        entry("user", "alice", "r", line=1),
        entry("user", "bob", "r", default=True, line=4),
    ])
    add(roll, "/srv/db", inode=10, is_dir=False)
    add(roll, "/srv/db/sub", inode=11, is_dir=True)
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.plan(["/srv/db", "/srv/db/sub"])
    assert exc.value.code == "ACL_DEFAULT_ON_FILE"
    assert exc.value.detail["path"] == "/srv/db"
    planned = roll.plan(["/srv/db/sub"])
    assert len(planned.diffs) == 1
    assert planned.diffs[0].desired_default == (atr.AclEntry("user", "1002", "r"),)


def test_missing_mask_is_recomputed_from_named_entries():
    roll = rollout()
    roll.add_template("/srv/a", [
        entry("user", "alice", "r"),
        entry("group", "ops", "x"),
    ])
    add(roll, "/srv/a", inode=20)
    diff = roll.plan(["/srv/a"]).diffs[0]
    assert diff.desired_access == (
        atr.AclEntry("user", "1001", "r"),
        atr.AclEntry("group", "2001", "x"),
        atr.AclEntry("mask", "", "rx"),
    )


def test_explicit_mask_is_kept_untouched():
    roll = rollout()
    roll.add_template("/srv/b", [
        entry("user", "alice", "rwx"),
        entry("mask", "", "r-x"),
    ])
    add(roll, "/srv/b", inode=21)
    diff = roll.plan(["/srv/b"]).diffs[0]
    assert diff.desired_access == (
        atr.AclEntry("user", "1001", "rwx"),
        atr.AclEntry("mask", "", "r-x"),
    )


def test_unknown_principal_reports_the_template_line():
    roll = rollout()
    roll.add_template("/srv/c", [
        entry("user", "alice", "r", line=6),
        entry("group", "ghosts", "r", line=7),
    ])
    add(roll, "/srv/c", inode=22)
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.plan(["/srv/c"])
    assert exc.value.code == "ACL_PRINCIPAL_UNKNOWN"
    assert exc.value.detail["line"] == 7
    assert exc.value.detail["name"] == "ghosts"


def test_plan_records_device_inode_and_fingerprints_in_order():
    roll = rollout()
    roll.add_template("/srv/d", [
        entry("user", "bob", "r"),
        entry("user", "alice", "w"),
        entry("group", "ops", "x"),
    ])
    before = add(roll, "/srv/d", inode=4242, dev=9)
    plan = roll.plan(["/srv/d"])
    diff = plan.diffs[0]
    assert plan.plan_id
    assert (diff.path, diff.dev, diff.inode, diff.is_dir) == ("/srv/d", 9, 4242, True)
    assert diff.current_fingerprint == roll.fingerprint(before.access)
    assert diff.current_fingerprint != diff.target_fingerprint
    assert diff.desired_access == (
        atr.AclEntry("user", "1001", "w"),
        atr.AclEntry("user", "1002", "r"),
        atr.AclEntry("group", "2001", "x"),
        atr.AclEntry("mask", "", "rwx"),
    )


def test_path_count_is_capped():
    roll = rollout(max_paths=2)
    roll.add_template("/srv/e", [entry("user", "alice", "r")])
    for index in range(3):
        add(roll, "/srv/e/%d" % index, inode=30 + index)
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.plan(["/srv/e/0", "/srv/e/1", "/srv/e/2"])
    assert exc.value.code == "ACL_PATH_LIMIT"


def test_path_depth_is_capped():
    roll = rollout(max_depth=2)
    roll.add_template("/srv", [entry("user", "alice", "r")])
    add(roll, "/srv/a", inode=40)
    add(roll, "/srv/a/b", inode=41)
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.plan(["/srv/a", "/srv/a/b"])
    assert exc.value.code == "ACL_DEPTH_LIMIT"


def test_template_entry_count_is_capped():
    roll = rollout(max_entries=2)
    roll.add_template("/srv/f", [
        entry("user", "alice", "r"),
        entry("user", "bob", "r"),
        entry("user", "carol", "r"),
    ])
    add(roll, "/srv/f", inode=50)
    with pytest.raises(atr.AclRolloutError) as exc:
        roll.plan(["/srv/f"])
    assert exc.value.code == "ACL_ENTRY_LIMIT"


def test_apply_journals_each_path_before_writing_it():
    store = {}
    seen = []

    def hook(path, acl):
        seen.append((path, [rec["kind"] for rec in roll.journal()]))

    roll = rollout(store, on_write=hook)
    roll.add_template("/srv/g", [entry("user", "alice", "r")])
    paths = ["/srv/g/0", "/srv/g/1", "/srv/g/2"]
    for index, path in enumerate(paths):
        add(roll, path, inode=60 + index)
    result = roll.apply(roll.plan(paths), now_ns=5)
    assert result.applied == tuple(paths)
    assert result.written == 3
    assert [path for path, _ in seen] == paths
    assert all(kinds[-1] == "begin" for _, kinds in seen)
    assert [rec["kind"] for rec in roll.journal()] == ["begin", "done"] * 3
    assert store[paths[0]].access == (atr.AclEntry("user", "1001", "r"),
                                      atr.AclEntry("mask", "", "r"))


def test_repeated_apply_writes_nothing_and_keeps_mtime():
    store = {}
    roll = rollout(store)
    roll.add_template("/srv/h", [entry("user", "alice", "r")])
    paths = ["/srv/h/0", "/srv/h/1"]
    for index, path in enumerate(paths):
        add(roll, path, inode=70 + index)
    plan = roll.plan(paths)
    first = roll.apply(plan, now_ns=111)
    assert first.written == 2
    stamps = {path: store[path].mtime_ns for path in paths}
    assert stamps == {path: 111 for path in paths}
    second = roll.apply(plan, now_ns=999)
    assert second.written == 0
    assert second.applied == ()
    assert {path: store[path].mtime_ns for path in paths} == stamps


def test_failure_midway_rolls_back_in_reverse_order():
    store = {}
    paths = ["/srv/i/0", "/srv/i/1", "/srv/i/2"]

    def hook(path, acl):
        if path == paths[2]:
            raise OSError("disk gone")

    roll = rollout(store, on_write=hook)
    roll.add_template("/srv/i", [entry("user", "alice", "r")])
    before = {path: add(roll, path, inode=80 + index)
              for index, path in enumerate(paths)}
    result = roll.apply(roll.plan(paths), now_ns=7)
    assert result.failed_path == paths[2]
    assert result.error_code == "ACL_WRITE_FAILED"
    assert result.applied == (paths[0], paths[1])
    assert result.rolled_back == (paths[2], paths[1], paths[0])
    assert {path: store[path] for path in paths} == before


def test_rollback_skips_paths_whose_acl_drifted():
    store = {}
    roll = rollout(store)
    roll.add_template("/srv/j", [entry("user", "alice", "r")])
    paths = ["/srv/j/0", "/srv/j/1", "/srv/j/2"]
    before = {path: add(roll, path, inode=90 + index)
              for index, path in enumerate(paths)}
    plan = roll.plan(paths)
    roll.apply(plan, now_ns=3)
    drifted = (atr.AclEntry("user", "9999", "rwx"),)
    add(roll, paths[1], inode=91, access=drifted)
    out = roll.rollback(plan)
    assert out.skipped == (paths[1],)
    assert out.reverted == (paths[2], paths[0])
    assert store[paths[1]].access == drifted
    assert store[paths[0]] == before[paths[0]]
    assert store[paths[2]] == before[paths[2]]


def test_recover_reverts_begin_without_done_after_a_crash():
    store = {}
    paths = ["/srv/k/0", "/srv/k/1", "/srv/k/2"]

    def hook(path, acl):
        if path == paths[1]:
            raise RuntimeError("killed")

    roll = rollout(store, on_write=hook)
    roll.add_template("/srv/k", [entry("user", "alice", "r")])
    before = {path: add(roll, path, inode=100 + index)
              for index, path in enumerate(paths)}
    with pytest.raises(RuntimeError):
        roll.apply(roll.plan(paths), now_ns=11)
    state = roll.snapshot()
    restarted = rollout(store)
    restarted.restore(state)
    reverted = restarted.recover()
    assert tuple(reverted) == (paths[1],)
    assert store[paths[1]] == before[paths[1]]
    assert store[paths[0]].access == (atr.AclEntry("user", "1001", "r"),
                                      atr.AclEntry("mask", "", "r"))
    assert store[paths[2]] == before[paths[2]]


def test_plan_skips_paths_already_matching_the_template():
    store = {}
    roll = rollout(store)
    roll.add_template("/srv/l", [entry("user", "alice", "r")])
    settled = (atr.AclEntry("user", "1001", "r"), atr.AclEntry("mask", "", "r"))
    add(roll, "/srv/l/done", inode=110, access=settled)
    add(roll, "/srv/l/todo", inode=111)
    plan = roll.plan(["/srv/l/done", "/srv/l/todo"])
    assert tuple(d.path for d in plan.diffs) == ("/srv/l/todo",)
    assert roll.diff(plan) == ()
