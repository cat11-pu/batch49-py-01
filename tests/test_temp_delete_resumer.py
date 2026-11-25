import errno
import threading

import pytest

import temp_delete_resumer as tdr


class FakeFS:
    def __init__(self, layout):
        self.layout = layout
        self.removed = []
        self.extra = {}
        self.fail = {}
        self.probe_calls = []
        self.listdir_calls = []

    def probe(self, path):
        self.probe_calls.append(path)
        item = self.layout.get(path)
        if item is None or path in self.removed:
            return None
        return {"dev": item.get("dev", 1), "inode": item["inode"],
                "kind": item["kind"]}

    def listdir(self, path):
        self.listdir_calls.append(path)
        item = self.layout.get(path) or {}
        return list(item.get("children", ())) + list(self.extra.get(path, ()))

    def remove(self, path):
        if path in self.fail:
            raise OSError(self.fail[path], "boom")
        self.removed.append(path)


def resumer(fs, **kwargs):
    kwargs.setdefault("root", "/t")
    return tdr.TempDeleteResumer(probe=fs.probe, listdir=fs.listdir,
                                 remove=fs.remove, **kwargs)


def tree_with_files(names):
    layout = {"/t": {"kind": "dir", "inode": 1, "children": list(names)}}
    for index, name in enumerate(names, start=2):
        layout["/t/" + name] = {"kind": "file", "inode": index}
    return layout


def test_plan_records_identity_and_post_order():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 10, "children": ["a", "b"]},
        "/t/a": {"kind": "dir", "inode": 11, "children": ["x"]},
        "/t/a/x": {"kind": "file", "inode": 12},
        "/t/b": {"kind": "file", "inode": 13},
    })
    res = resumer(fs)
    out = res.plan()
    assert out == {"root": "/t", "entries": 4, "skipped": 0,
                   "subplans": 1, "state": "planned"}
    entries = res.entries()
    assert [e["path"] for e in entries] == ["/t/a/x", "/t/a", "/t/b", "/t"]
    assert [e["seq"] for e in entries] == [1, 2, 3, 4]
    assert [e["depth"] for e in entries] == [2, 1, 1, 0]
    assert (entries[0]["dev"], entries[0]["inode"]) == (1, 12)
    assert entries[2]["kind"] == "file"


def test_plan_rejects_tree_deeper_than_limit():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 1, "children": ["a"]},
        "/t/a": {"kind": "dir", "inode": 2, "children": ["b"]},
        "/t/a/b": {"kind": "dir", "inode": 3, "children": []},
    })
    res = resumer(fs, max_depth=1)
    with pytest.raises(tdr.DeleteResumeError) as excinfo:
        res.plan()
    assert excinfo.value.code == "DEPTH_LIMIT"
    assert excinfo.value.detail == "/t/a/b"


def test_large_directory_is_split_into_subplans():
    fs = FakeFS(tree_with_files(["f1", "f2", "f3", "f4", "f5"]))
    res = resumer(fs, subplan_size=2)
    res.plan()
    assert len(res.entries()) == 6
    plans = res.subplans()
    assert [(p["index"], p["first_seq"], p["last_seq"], p["count"])
            for p in plans] == [(1, 1, 2, 2), (2, 3, 4, 2), (3, 5, 6, 2)]


def test_resume_deletes_children_before_parent():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 1, "children": ["d", "e"]},
        "/t/d": {"kind": "dir", "inode": 2, "children": ["k"]},
        "/t/d/k": {"kind": "file", "inode": 3},
        "/t/e": {"kind": "file", "inode": 4},
    })
    res = resumer(fs)
    res.plan()
    out = res.resume()
    assert out["state"] == "done"
    assert out["deleted"] == 4
    assert out["remaining"] == 0
    assert fs.removed == ["/t/d/k", "/t/d", "/t/e", "/t"]


def test_inode_mismatch_marks_subtree_replaced():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 1, "children": ["a"]},
        "/t/a": {"kind": "dir", "inode": 2, "children": ["f"]},
        "/t/a/f": {"kind": "file", "inode": 3},
    })
    res = resumer(fs)
    res.plan()
    fs.layout["/t/a"]["inode"] = 99
    out = res.resume()
    assert out["state"] == "partial"
    assert out["deleted"] == 0
    assert out["replaced"] == ["/t/a"]
    assert fs.removed == []


def test_cancel_keeps_deleted_and_resume_continues():
    fs = FakeFS(tree_with_files(["f1", "f2"]))
    res = resumer(fs)
    res.plan()
    original = fs.remove
    calls = {"n": 0}

    def remove(path):
        calls["n"] += 1
        original(path)
        if calls["n"] == 1:
            res.cancel("operator")

    fs.remove = remove
    res = resumer(fs)
    res.plan()
    out = res.resume()
    assert out["state"] == "cancelled"
    assert out["deleted"] == 1
    assert out["remaining"] == 2
    resumed = res.resume()
    assert resumed["state"] == "done"
    assert resumed["deleted"] == 2
    assert fs.removed == ["/t/f1", "/t/f2", "/t"]


def test_resume_after_done_is_idempotent():
    fs = FakeFS(tree_with_files(["f1"]))
    res = resumer(fs)
    res.plan()
    assert res.resume()["state"] == "done"
    again = res.resume()
    assert again == {"state": "done", "deleted": 0, "remaining": 0,
                     "skipped": [], "replaced": [], "busy": [], "errors": []}


def test_permission_error_is_classified_and_others_continue():
    fs = FakeFS(tree_with_files(["f1", "f2"]))
    fs.fail["/t/f2"] = errno.EACCES
    res = resumer(fs)
    res.plan()
    out = res.resume()
    assert out["state"] == "partial"
    assert out["deleted"] == 2
    assert [(e["seq"], e["path"], e["code"]) for e in out["errors"]] == [
        (2, "/t/f2", "permission")]
    assert "/t/f2" not in fs.removed
    fs.fail.clear()
    retry = res.resume()
    assert retry["state"] == "done"
    assert retry["deleted"] == 1


def test_busy_errno_is_classified_separately():
    fs = FakeFS(tree_with_files(["f1", "f2"]))
    fs.fail["/t/f1"] = errno.EBUSY
    fs.fail["/t/f2"] = errno.EIO
    res = resumer(fs)
    res.plan()
    out = res.resume()
    codes = {e["path"]: e["code"] for e in out["errors"]}
    assert codes == {"/t/f1": "busy", "/t/f2": "other"}


def test_directory_with_new_child_is_marked_busy():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 1, "children": ["a"]},
        "/t/a": {"kind": "dir", "inode": 2, "children": ["f"]},
        "/t/a/f": {"kind": "file", "inode": 3},
    })
    res = resumer(fs)
    res.plan()
    fs.extra["/t/a"] = ["new"]
    out = res.resume()
    assert out["busy"] == ["/t/a"]
    assert out["deleted"] == 2
    assert out["remaining"] == 1
    assert "/t/a" not in fs.removed
    assert out["state"] == "partial"


def test_symlinks_and_mount_points_are_skipped():
    fs = FakeFS({
        "/t": {"kind": "dir", "inode": 1, "children": ["l", "m"]},
        "/t/l": {"kind": "link", "inode": 5},
        "/t/m": {"kind": "mount", "inode": 6},
    })
    res = resumer(fs)
    assert res.plan()["skipped"] == 2
    out = res.resume()
    assert out["skipped"] == ["/t/l", "/t/m"]
    assert out["deleted"] == 1
    assert fs.removed == ["/t"]
    assert out["state"] == "done"


def test_only_one_resume_executes_at_a_time():
    fs = FakeFS(tree_with_files(["f1", "f2"]))
    res = resumer(fs)
    res.plan()
    entered = threading.Event()
    gate = threading.Event()
    original = fs.remove

    def slow_remove(path):
        if not entered.is_set():
            entered.set()
            assert gate.wait(1)
        original(path)

    fs.remove = slow_remove
    res = resumer(fs)
    res.plan()
    result = {}

    def run():
        result["out"] = res.resume()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert entered.wait(1)
    second = res.resume()
    assert second["state"] == "running"
    gate.set()
    worker.join(1)
    assert result["out"]["state"] == "done"
    assert fs.removed == ["/t/f1", "/t/f2"]


def test_progress_log_writes_intent_before_delete():
    fs = FakeFS(tree_with_files(["f1"]))
    res = resumer(fs)
    res.plan()
    res.resume()
    records = res.log_records()
    actions = [r["action"] for r in records if r["path"] == "/t/f1"]
    assert actions == ["intent", "deleted"]
    assert [r["action"] for r in records if r["path"] == "/t"] == [
        "intent", "deleted"]


def test_snapshot_and_restore_keep_progress():
    fs = FakeFS(tree_with_files(["f1", "f2", "f3"]))
    res = resumer(fs)
    res.plan()
    original = fs.remove
    calls = {"n": 0}

    def remove(path):
        calls["n"] += 1
        original(path)
        if calls["n"] == 1:
            res.cancel("pause")

    fs.remove = remove
    res = resumer(fs)
    res.plan()
    res.resume()
    snap = res.snapshot()

    fs.remove = original
    restarted = resumer(fs)
    restarted.restore(snap)
    status = restarted.status()
    assert status["state"] == "cancelled"
    assert (status["planned"], status["deleted"], status["remaining"]) == (4, 1, 3)
    final = restarted.resume()
    assert final["state"] == "done"
    assert final["deleted"] == 3
    assert fs.removed == ["/t/f1", "/t/f2", "/t/f3", "/t"]


def test_invalid_snapshot_is_rejected_without_state_change():
    fs = FakeFS(tree_with_files(["f1"]))
    res = resumer(fs)
    res.plan()
    with pytest.raises(tdr.DeleteResumeError) as excinfo:
        res.restore({})
    assert excinfo.value.code == "PLAN_INVALID"
    assert res.status()["state"] == "planned"
