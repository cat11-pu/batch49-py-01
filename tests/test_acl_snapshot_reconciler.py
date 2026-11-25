import hashlib

import pytest

import acl_snapshot_reconciler as asr


def put(reconciler, path, inode, *, access=(), default=(), owner_uid=1001,
        owner_gid=2001, dev=8, is_dir=True):
    return reconciler.put(path, dev=dev, inode=inode, owner_uid=owner_uid,
                          owner_gid=owner_gid, access=access, default=default,
                          is_dir=is_dir)


def test_export_bytes_are_stable_across_runs():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    put(rec, "team/b", 2, access=("user:1001:rwx",), default=("default:user:1001:r",))
    first = rec.snapshot("team")
    second = rec.snapshot("team")
    assert rec.export_bytes(first) == rec.export_bytes(second)
    assert first.digest == hashlib.sha256(rec.export_bytes(first)).hexdigest()
    reloaded = rec.import_bytes(rec.export_bytes(first))
    assert reloaded.entries == first.entries
    assert reloaded.root == "team"


def test_snapshot_sorts_paths_and_records_identity():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/z", 30, access=("user:1001:r",), dev=9)
    put(rec, "team/a", 10, access=("user:1001:r",))
    snap = rec.snapshot("team")
    assert tuple(row.path for row in snap.entries) == ("team/a", "team/z")
    head = snap.entries[0]
    assert (head.dev, head.inode, head.owner_uid, head.owner_gid) == (8, 10, 1001, 2001)
    plain = asr.Record("team/a", 8, 10, 1001, 2001, ("user:1001:r",), ())
    defaulted = asr.Record("team/a", 8, 10, 1001, 2001, ("user:1001:r",),
                           ("user:1001:r",))
    assert rec.fingerprint(plain) != rec.fingerprint(defaulted)


def test_unchanged_paths_are_skipped_without_writes():
    writes = []
    rec = asr.AclSnapshotReconciler(on_write=lambda path, row: writes.append(path))
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    report = rec.restore(snap)
    assert report.applied == ()
    assert report.created == ()
    assert report.skipped == ("team/a",)
    assert writes == []


def test_replaced_inode_is_reported_and_never_overwritten():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 2, access=("user:1001:rwx",))
    report = rec.restore(snap)
    assert report.applied == ()
    assert len(report.conflicted) == 1
    conflict = report.conflicted[0]
    assert (conflict.path, conflict.reason) == ("team/a", "INODE_CHANGED")
    assert conflict.current.inode == 2
    assert conflict.snapshot.inode == 1
    assert rec.snapshot("team").entries[0].access == ("user:1001:rwx",)


def test_missing_path_is_not_created_by_default():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    rec.drop("team/a")
    report = rec.restore(snap)
    assert report.skipped == ("team/a",)
    assert report.created == ()
    assert rec.snapshot("team").entries == ()


def test_allow_create_restores_missing_paths():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",), default=("user:1001:r",))
    snap = rec.snapshot("team")
    rec.drop("team/a")
    report = rec.restore(snap, allow_create=True)
    assert report.created == ("team/a",)
    assert report.applied == ()
    fresh = rec.snapshot("team").entries[0]
    assert fresh.inode == 1
    assert fresh.access == ("user:1001:r",)
    assert fresh.default == ("user:1001:r",)


def test_extra_entries_default_to_keep():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 1, access=("user:1001:rwx", "user:1002:r"))
    report = rec.restore(snap)
    assert report.applied == ("team/a",)
    assert rec.snapshot("team").entries[0].access == ("user:1001:r", "user:1002:r")


def test_extra_entries_merge_unions_same_key_permissions():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 1, access=("user:1001:rwx", "user:1002:r"))
    rec.restore(snap, policy="merge")
    assert rec.snapshot("team").entries[0].access == ("user:1001:rwx", "user:1002:r")


def test_replace_policy_drops_extra_entries():
    rec = asr.AclSnapshotReconciler()
    put(rec, "team/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 1, access=("user:1001:rwx", "user:1002:r"))
    rec.restore(snap, policy="replace")
    assert rec.snapshot("team").entries[0].access == ("user:1001:r",)


def test_access_and_default_are_restored_independently():
    access_only = asr.AclSnapshotReconciler()
    put(access_only, "team/a", 1, access=("user:1001:rwx",),
        default=("user:1001:r",))
    snap = access_only.snapshot("team")
    put(access_only, "team/a", 1, access=("user:1001:r",),
        default=("user:1001:r",))
    report = access_only.restore(snap)
    assert report.applied == ("team/a",)
    row = access_only.snapshot("team").entries[0]
    assert row.access == ("user:1001:rwx",)
    assert row.default == ("user:1001:r",)

    default_only = asr.AclSnapshotReconciler()
    put(default_only, "team/a", 1, access=("user:1001:r",),
        default=("user:1001:r",))
    default_snap = default_only.snapshot("team")
    put(default_only, "team/a", 1, access=("user:1001:r",),
        default=("user:1001:rwx",))
    default_report = default_only.restore(default_snap)
    assert default_report.applied == ("team/a",)
    default_row = default_only.snapshot("team").entries[0]
    assert default_row.access == ("user:1001:r",)
    assert default_row.default == ("user:1001:r",)


def test_interrupted_restore_resumes_without_rewriting():
    paths = ["team/x/1", "team/x/2", "team/x/3"]

    def failing(path, row):
        if path == paths[1]:
            raise OSError("write failed")

    rec = asr.AclSnapshotReconciler(on_write=failing)
    for index, path in enumerate(paths):
        put(rec, path, index + 1, access=("user:1001:r",))
    snap = rec.snapshot("team/x")
    for index, path in enumerate(paths):
        put(rec, path, index + 1, access=("user:1001:rwx",))
    report = rec.restore(snap)
    assert report.interrupted is True
    assert report.applied == (paths[0],)
    state = rec.save_state()

    later_writes = []
    restarted = asr.AclSnapshotReconciler(
        on_write=lambda path, row: later_writes.append(path))
    restarted.load_state(state)
    resumed = restarted.resume()
    assert resumed.applied == (paths[1], paths[2])
    assert later_writes == [paths[1], paths[2]]
    assert resumed.interrupted is False


def test_conflict_cap_aborts_the_whole_batch():
    writes = []
    rec = asr.AclSnapshotReconciler(max_conflicts=1,
                                    on_write=lambda path, row: writes.append(path))
    put(rec, "team/a", 1, access=("user:1001:r",))
    put(rec, "team/b", 2, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 11, access=("user:1001:r",))
    put(rec, "team/b", 12, access=("user:1001:r",))
    with pytest.raises(asr.AclSnapshotError) as exc:
        rec.restore(snap)
    assert exc.value.code == "ACL_CONFLICT_LIMIT"
    assert writes == []
    entries = {row.path: row for row in rec.snapshot("team").entries}
    assert entries["team/a"].inode == 11
    assert entries["team/b"].inode == 12


def test_unknown_owner_is_conflicted_not_written_as_nobody():
    rec = asr.AclSnapshotReconciler(known_uids={1001}, known_gids={2001})
    put(rec, "team/a", 1, access=("user:1001:r",), owner_uid=4242)
    snap = rec.snapshot("team")
    put(rec, "team/a", 1, access=("user:1001:r",), owner_uid=1001)
    report = rec.restore(snap)
    assert report.applied == ()
    assert [item.reason for item in report.conflicted] == ["OWNER_UNKNOWN"]
    assert report.conflicted[0].snapshot.owner_uid == 4242
    assert rec.snapshot("team").entries[0].owner_uid == 1001


def test_busy_segment_blocks_restore_until_released():
    writes = []
    rec = asr.AclSnapshotReconciler(on_write=lambda path, row: writes.append(path))
    put(rec, "team/proj/a", 1, access=("user:1001:r",))
    snap = rec.snapshot("team/proj")
    put(rec, "team/proj/a", 1, access=("user:1001:rwx",))
    rec.acquire_segment("team")
    with pytest.raises(asr.AclSnapshotError) as exc:
        rec.restore(snap)
    assert exc.value.code == "ACL_SEGMENT_BUSY"
    assert writes == []
    rec.release_segment("team")
    report = rec.restore(snap)
    assert report.applied == ("team/proj/a",)
    assert writes == ["team/proj/a"]


def test_compare_reports_only_the_conflicting_paths():
    writes = []
    rec = asr.AclSnapshotReconciler(on_write=lambda path, row: writes.append(path))
    put(rec, "team/a", 1, access=("user:1001:r",))
    put(rec, "team/b", 2, access=("user:1001:r",))
    snap = rec.snapshot("team")
    put(rec, "team/a", 1, access=("user:1001:r",))
    put(rec, "team/b", 22, access=("user:1001:r",))
    conflicts = rec.compare(snap)
    assert [(item.path, item.reason) for item in conflicts] == [("team/b", "INODE_CHANGED")]
    assert writes == []
    live = {row.path: row for row in rec.snapshot("team").entries}
    assert live["team/b"].inode == 22
