import copy

import pytest

import overmount_drift_detector as od


def row(mount_id, parent_id, path, *, device="sda1", fs_type="ext4",
        mounted_at=100.0, namespace="host"):
    return {
        "mount_id": mount_id,
        "parent_id": parent_id,
        "path": path,
        "device": device,
        "fs_type": fs_type,
        "mounted_at": mounted_at,
        "namespace": namespace,
    }


class Clock:
    def __init__(self, value=0.0):
        self.value = value

    def __call__(self):
        return self.value


def test_baseline_records_identity_and_version():
    detector = od.OvermountDriftDetector()
    out = detector.baseline([row(1, 0, "/srv"), row(2, 1, "/srv/data")])
    assert out == {"count": 2, "version": 1}
    baseline = detector.snapshot()["baseline"]
    assert baseline["host:/srv"]["mount_id"] == 1
    assert baseline["host:/srv"]["device"] == "sda1"
    assert baseline["host:/srv"]["fs_type"] == "ext4"
    assert baseline["host:/srv"]["mounted_at"] == 100.0
    assert baseline["host:/srv/data"]["parent_id"] == 1
    assert detector.stats() == {"scans": 0, "alerts": 0, "confirmed": 0,
                                "recovered": 0, "rejected": 0}


def test_mount_tree_orders_parent_before_child():
    detector = od.OvermountDriftDetector()
    rows = [row(3, 2, "/srv/data/log"), row(1, 0, "/srv"),
            row(2, 1, "/srv/data")]
    tree = detector.mount_tree(rows)
    assert tree[1]["depth"] == 0
    assert tree[2]["depth"] == 1
    assert tree[3]["depth"] == 2
    assert tree[1]["children"] == [2]
    assert tree[2]["children"] == [3]
    assert tree[3]["path"] == "/srv/data/log"


def test_orphan_mounts_listed_separately():
    detector = od.OvermountDriftDetector()
    result = detector.scan([row(1, 0, "/srv"), row(5, 99, "/lost")])
    assert [entry["mount_id"] for entry in result["entries"]] == [1]
    assert len(result["orphans"]) == 1
    assert result["orphans"][0]["mount_id"] == 5
    assert result["orphans"][0]["missing_parent"] == 99
    assert result["truncated"] == 0


def test_scan_rejects_duplicate_mount_id_and_keeps_state():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv")])
    before = detector.snapshot()["baseline"]
    with pytest.raises(od.DriftError) as exc:
        detector.scan([row(1, 0, "/srv"), row(1, 0, "/other")])
    assert exc.value.code == "TABLE_INCONSISTENT"
    assert detector.snapshot()["baseline"] == before
    assert detector.stats()["rejected"] == 1
    result = detector.scan([row(1, 0, "/srv")])
    assert result["entries"][0]["state"] == "unchanged"


def test_scan_rejects_parent_cycle():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv")])
    with pytest.raises(od.DriftError) as exc:
        detector.scan([row(1, 2, "/a"), row(2, 1, "/b")])
    assert exc.value.code == "TABLE_INCONSISTENT"
    assert detector.stats()["rejected"] == 1
    assert detector.snapshot()["baseline"]["host:/srv"]["mount_id"] == 1


def test_new_mount_reported_without_overmount():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv")])
    result = detector.scan([row(1, 0, "/srv"),
                            row(2, 1, "/srv/data", device="sdb1")])
    states = {entry["path"]: entry for entry in result["entries"]}
    assert states["/srv"]["state"] == "unchanged"
    assert states["/srv/data"]["state"] == "new"
    assert states["/srv/data"]["overmount"] is False
    diff = detector.diff()
    assert [entry["path"] for entry in diff["new"]] == ["/srv/data"]
    assert diff["overmount"] == []


def test_overmount_reports_covered_path():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv", device="sda1")])
    result = detector.scan([row(9, 0, "/srv", device="sdb1")])
    entry = result["entries"][0]
    assert entry["state"] == "overmount"
    assert entry["overmount"] is True
    assert entry["covered_path"] == "/srv"
    diff = detector.diff()
    assert [item["path"] for item in diff["overmount"]] == ["/srv"]
    assert diff["overmount"][0]["covered_path"] == "/srv"


def test_vanished_keeps_baseline_until_confirm():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv"), row(2, 1, "/srv/data")])
    detector.scan([row(1, 0, "/srv")])
    diff = detector.diff()
    assert [item["path"] for item in diff["vanished"]] == ["/srv/data"]
    assert "host:/srv/data" in detector.snapshot()["baseline"]
    detector.scan([row(1, 0, "/srv")])
    assert [item["path"] for item in detector.diff()["vanished"]] == ["/srv/data"]
    assert detector.stats()["confirmed"] == 0


def test_confirm_commits_last_scan():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv"), row(2, 1, "/srv/data")])
    detector.scan([row(1, 0, "/srv")])
    assert detector.confirm() == 1
    assert detector.diff()["vanished"] == []
    assert detector.snapshot()["baseline"].keys() == {"host:/srv"}
    result = detector.scan([row(1, 0, "/srv")])
    assert result["entries"][0]["state"] == "unchanged"
    assert detector.stats()["confirmed"] == 1
    assert detector.stats()["scans"] == 2


def test_same_id_new_mounted_at_is_reused():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv", mounted_at=100.0)])
    result = detector.scan([row(1, 0, "/srv", mounted_at=200.0)])
    assert result["entries"][0]["state"] == "reused"
    assert result["entries"][0]["overmount"] is False
    assert [item["path"] for item in detector.diff()["reused"]] == ["/srv"]


def test_alert_window_dedupes_same_drift():
    clock = Clock(0.0)
    detector = od.OvermountDriftDetector(alert_window=5.0, clock=clock)
    detector.baseline([row(1, 0, "/srv")])
    clock.value = 1.0
    detector.scan([row(9, 0, "/srv")])
    assert len(detector.alerts()) == 1
    clock.value = 3.0
    detector.scan([row(9, 0, "/srv")])
    assert len(detector.alerts()) == 1
    clock.value = 10.0
    detector.scan([row(9, 0, "/srv")])
    assert len(detector.alerts()) == 2
    assert detector.alerts()[0]["path"] == "/srv"
    assert detector.alerts()[0]["state"] == "overmount"


def test_entry_cap_truncates_with_count():
    detector = od.OvermountDriftDetector(max_entries=2)
    rows = [row(index, 0, f"/m{index}") for index in range(1, 5)]
    result = detector.scan(rows)
    assert [entry["path"] for entry in result["entries"]] == ["/m1", "/m2"]
    assert result["truncated"] == 2


def test_namespaces_compared_separately():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv", namespace="host"),
                       row(2, 0, "/srv", namespace="container:abc")])
    result = detector.scan([row(1, 0, "/srv", namespace="host"),
                            row(9, 0, "/srv", namespace="container:abc")])
    by_namespace = {entry["namespace"]: entry for entry in result["entries"]}
    assert by_namespace["host"]["state"] == "unchanged"
    assert by_namespace["container:abc"]["state"] == "overmount"
    assert by_namespace["container:abc"]["covered_path"] == "/srv"


def test_recover_requires_known_drift_and_marks_once():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv")])
    detector.scan([row(9, 0, "/srv")])
    out = detector.recover("/srv")
    assert out["recovered"] is True
    assert out["state"] == "overmount"
    assert detector.stats()["recovered"] == 1
    with pytest.raises(od.DriftError) as exc:
        detector.recover("/srv")
    assert exc.value.code == "NOT_DRIFTED"


def test_snapshot_restore_roundtrip_and_invalid_state():
    detector = od.OvermountDriftDetector()
    detector.baseline([row(1, 0, "/srv")])
    detector.scan([row(1, 0, "/srv"), row(2, 1, "/srv/data")])
    state = detector.snapshot()
    assert {"baseline", "alerts", "stats"} <= set(state)
    pristine = copy.deepcopy(state)
    state["baseline"].clear()
    state["alerts"].clear()
    assert "host:/srv" in detector.snapshot()["baseline"]
    assert len(detector.alerts()) == 1

    clone = od.OvermountDriftDetector()
    clone.restore(pristine)
    assert clone.snapshot()["baseline"] == detector.snapshot()["baseline"]
    assert clone.stats() == detector.stats()
    assert [item["path"] for item in clone.diff()["new"]] == ["/srv/data"]

    bad_states = [None, {}, {"baseline": [], "alerts": [], "stats": {}},
                  {"baseline": {}, "alerts": "nope", "stats": {}},
                  {"baseline": {}, "alerts": [], "stats": []}]
    for bad in bad_states:
        with pytest.raises(od.DriftError) as exc:
            clone.restore(bad)
        assert exc.value.code == "STATE_INVALID"
    assert "host:/srv" in clone.snapshot()["baseline"]
