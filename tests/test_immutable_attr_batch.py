import threading

import pytest

import immutable_attr_batch as iab

ROOT = "/srv/app"


def batch(**kwargs):
    kwargs.setdefault("allowed_roots", (ROOT,))
    return iab.ImmutableAttrBatch(**kwargs)


def credential(paths, *, actions=("set",), digests=None):
    digests = digests or {}
    return {
        "id": "cred-1",
        "actions": tuple(actions),
        "paths": {path: digests.get(path, "") for path in paths},
    }


def test_add_path_registers_inventory_entry_and_attributes():
    obj = batch()
    node = obj.add_path("/srv/app/job.log")
    assert node.path == "/srv/app/job.log"
    assert node.kind == "file"
    assert node.parent == ROOT
    assert node.immutable is False
    assert node.fs_immutable is True
    assert node.mount is False
    assert isinstance(node.inode, int) and node.inode > 0
    assert obj.attributes("/srv/app/job.log") == {
        "path": "/srv/app/job.log",
        "kind": "file",
        "inode": node.inode,
        "parent": ROOT,
        "immutable": False,
        "fs_immutable": True,
        "mount": False,
        "digest": "",
    }
    with pytest.raises(iab.ImmutableError) as exc:
        obj.attributes("/srv/app/missing")
    assert exc.value.code == "PATH_UNKNOWN"


def test_plan_rejects_path_outside_allowed_roots():
    obj = batch()
    obj.add_path("/etc/passwd")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.plan(["/etc/passwd"], action="set")
    assert exc.value.code == "PATH_NOT_ALLOWED"
    assert exc.value.detail == "/etc/passwd"


def test_plan_refuses_symlink():
    obj = batch()
    obj.add_path("/srv/app/link", kind="symlink")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.plan(["/srv/app/link"], action="set")
    assert exc.value.code == "SYMLINK_REFUSED"
    assert exc.value.detail == "/srv/app/link"


def test_plan_orders_parent_before_child_for_set():
    obj = batch()
    obj.add_path("/srv/app/data", kind="dir")
    obj.add_path("/srv/app/data/out.bin")
    plan = obj.plan(["/srv/app/data/out.bin", "/srv/app/data"], action="set")
    assert plan.action == "set"
    assert plan.ordered == ("/srv/app/data", "/srv/app/data/out.bin")
    assert plan.skipped == ()


def test_strict_order_rejects_inverted_sequence():
    obj = batch()
    obj.add_path("/srv/app/data", kind="dir")
    obj.add_path("/srv/app/data/out.bin")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.plan(["/srv/app/data/out.bin", "/srv/app/data"],
                 action="set", strict_order=True)
    assert exc.value.code == "ORDER_VIOLATION"


def test_credential_must_cover_every_path_and_action():
    obj = batch()
    obj.add_path("/srv/app/a.txt")
    obj.add_path("/srv/app/b.txt")
    partial = credential(["/srv/app/a.txt"])
    report = obj.credential_check(
        ["/srv/app/a.txt", "/srv/app/b.txt"], partial, action="set")
    assert report.ok is False
    assert report.missing == ("/srv/app/b.txt",)
    assert report.action_ok is True
    assert report.digest_ok is True

    with pytest.raises(iab.ImmutableError) as exc:
        obj.set(["/srv/app/a.txt", "/srv/app/b.txt"], credential=partial)
    assert exc.value.code == "CREDENTIAL_INCOMPLETE"
    assert exc.value.detail == ("/srv/app/b.txt",)
    assert obj.attributes("/srv/app/a.txt")["immutable"] is False

    obj.add_path("/srv/app/locked.txt", immutable=True)
    set_only = credential(["/srv/app/locked.txt"], actions=("set",))
    with pytest.raises(iab.ImmutableError) as exc:
        obj.unset(["/srv/app/locked.txt"], credential=set_only)
    assert exc.value.code == "CREDENTIAL_ACTION_DENIED"
    assert obj.attributes("/srv/app/locked.txt")["immutable"] is True


def test_set_refuses_digest_mismatch_before_touching_attributes():
    obj = batch()
    obj.add_path("/srv/app/a.txt", digest="sha-old")
    bad = credential(["/srv/app/a.txt"],
                     digests={"/srv/app/a.txt": "sha-new"})
    report = obj.credential_check(["/srv/app/a.txt"], bad, action="set")
    assert report.digest_ok is False
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set(["/srv/app/a.txt"], credential=bad)
    assert exc.value.code == "DIGEST_MISMATCH"
    assert exc.value.detail == ("/srv/app/a.txt",)
    assert obj.attributes("/srv/app/a.txt")["immutable"] is False
    assert obj.stats()["applied"] == 0


def test_set_applies_batch_and_verify_reads_back():
    obj = batch()
    obj.add_path("/srv/app/data", kind="dir")
    obj.add_path("/srv/app/data/out.bin")
    cred = credential(["/srv/app/data", "/srv/app/data/out.bin"])
    result = obj.set(["/srv/app/data/out.bin", "/srv/app/data"], credential=cred)
    assert result.action == "set"
    assert result.applied == ("/srv/app/data", "/srv/app/data/out.bin")
    assert result.skipped == ()
    assert result.rolled_back == 0
    assert obj.verify("/srv/app/data") == {
        "path": "/srv/app/data",
        "inode": obj.attributes("/srv/app/data")["inode"],
        "immutable": True,
        "readback": True,
    }
    assert obj.attributes("/srv/app/data/out.bin")["immutable"] is True
    assert obj.stats() == {"applied": 2, "skipped": 0, "unset": 0,
                           "rolled_back": 0, "conflicts": 0, "recovered": 0}
    entries = obj.journal_entries()
    assert [entry["op"] for entry in entries] == ["set", "set"]
    assert [entry["path"] for entry in entries] == [
        "/srv/app/data", "/srv/app/data/out.bin"]
    assert [entry["state"] for entry in entries] == ["done", "done"]


def test_set_rejects_directory_that_shadows_children():
    obj = batch()
    obj.add_path("/srv/app/data", kind="dir")
    obj.add_path("/srv/app/data/a.txt")
    obj.add_path("/srv/app/data/b.txt")
    cred = credential(["/srv/app/data"])
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set(["/srv/app/data"], credential=cred)
    assert exc.value.code == "SHADOWED_CHILDREN"
    assert exc.value.detail == ("/srv/app/data/a.txt", "/srv/app/data/b.txt")
    assert obj.attributes("/srv/app/data")["immutable"] is False


def test_unset_restores_mutability_and_counts_skips():
    obj = batch()
    obj.add_path("/srv/app/a.txt", immutable=True)
    obj.add_path("/srv/app/b.txt", immutable=True)
    cred = credential(["/srv/app/a.txt", "/srv/app/b.txt"],
                      actions=("unset",))
    result = obj.unset(["/srv/app/b.txt", "/srv/app/a.txt"], credential=cred)
    assert result.action == "unset"
    assert result.applied == ("/srv/app/a.txt", "/srv/app/b.txt")
    assert obj.attributes("/srv/app/a.txt")["immutable"] is False
    assert obj.attributes("/srv/app/b.txt")["immutable"] is False

    again = obj.unset(["/srv/app/a.txt", "/srv/app/b.txt"], credential=cred)
    assert again.applied == ()
    assert again.skipped == ("/srv/app/a.txt", "/srv/app/b.txt")
    assert obj.stats() == {"applied": 0, "skipped": 2, "unset": 2,
                           "rolled_back": 0, "conflicts": 0, "recovered": 0}


def test_failure_rolls_back_prior_paths_in_reverse():
    obj = batch()
    names = ("a.txt", "b.txt", "c.txt")
    for name in names:
        obj.add_path(f"/srv/app/{name}")
    cred = credential([f"/srv/app/{name}" for name in names])
    obj.inject_fault("/srv/app/c.txt", code="READBACK_MISMATCH",
                     when="apply")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set([f"/srv/app/{name}" for name in names], credential=cred)
    assert exc.value.code == "READBACK_MISMATCH"
    for name in names:
        assert obj.attributes(f"/srv/app/{name}")["immutable"] is False
    assert obj.stats() == {"applied": 0, "skipped": 0, "unset": 0,
                           "rolled_back": 2, "conflicts": 0, "recovered": 0}
    entries = obj.journal_entries()
    assert [entry["path"] for entry in entries] == [
        "/srv/app/a.txt", "/srv/app/b.txt", "/srv/app/c.txt"]
    assert [entry["state"] for entry in entries] == [
        "rolled_back", "rolled_back", "failed"]


def test_inode_change_marks_conflict_and_rolls_back():
    obj = batch()
    obj.add_path("/srv/app/a.txt")
    obj.add_path("/srv/app/b.txt")
    cred = credential(["/srv/app/a.txt", "/srv/app/b.txt"])
    obj.inject_fault("/srv/app/b.txt", code="INODE_CHANGE", when="apply")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set(["/srv/app/a.txt", "/srv/app/b.txt"], credential=cred)
    assert exc.value.code == "CONFLICT"
    assert exc.value.detail == "/srv/app/b.txt"
    assert obj.stats()["conflicts"] == 1
    assert obj.stats()["rolled_back"] == 1
    assert obj.attributes("/srv/app/a.txt")["immutable"] is False
    assert obj.attributes("/srv/app/b.txt")["immutable"] is False


def test_path_count_and_depth_limits_are_enforced():
    obj = batch(max_paths=3, max_depth=2)
    names = ("a.txt", "b.txt", "c.txt", "d.txt")
    for name in names:
        obj.add_path(f"/srv/app/{name}")
    cred = credential([f"/srv/app/{name}" for name in names])
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set([f"/srv/app/{name}" for name in names], credential=cred)
    assert exc.value.code == "PATH_LIMIT"

    obj.add_path("/srv/app/x/y/z/deep.bin")
    with pytest.raises(iab.ImmutableError) as exc:
        obj.plan(["/srv/app/x/y/z/deep.bin"], action="set")
    assert exc.value.code == "DEPTH_LIMIT"


def test_recover_replays_interrupted_batch():
    obj = batch()
    names = ("a.txt", "b.txt", "c.txt", "d.txt")
    for name in names:
        obj.add_path(f"/srv/app/{name}")
    cred = credential([f"/srv/app/{name}" for name in names])
    obj.crash_after(2)
    with pytest.raises(iab.ImmutableError) as exc:
        obj.set([f"/srv/app/{name}" for name in names], credential=cred)
    assert exc.value.code == "CRASH_SIMULATED"
    assert obj.attributes("/srv/app/a.txt")["immutable"] is True
    assert obj.attributes("/srv/app/c.txt")["immutable"] is False

    report = obj.recover()
    assert report == {"unfinished": 4, "rolled_back": 2, "conflicts": 0}
    for name in names:
        assert obj.attributes(f"/srv/app/{name}")["immutable"] is False
    assert [entry["state"] for entry in obj.journal_entries()] == [
        "rolled_back", "rolled_back", "cancelled", "cancelled"]
    assert obj.stats()["recovered"] == 2


def test_same_path_mutations_are_serialized():
    obj = batch()
    paths = ("/srv/app/a.txt", "/srv/app/b.txt")
    for path in paths:
        obj.add_path(path)
    cred = credential(paths, actions=("set", "unset"))
    errors = []
    barrier = threading.Barrier(6)

    def worker():
        try:
            barrier.wait(timeout=5)
            for _ in range(5):
                obj.set(paths, credential=cred)
                obj.unset(paths, credential=cred)
        except Exception as exc:  # pragma: no cover - reported via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    stats = obj.stats()
    assert stats["applied"] + stats["unset"] + stats["skipped"] == 120
    assert stats["rolled_back"] == 0
    assert len(obj.journal_entries()) == 120
    for path in paths:
        assert obj.attributes(path)["immutable"] is False
