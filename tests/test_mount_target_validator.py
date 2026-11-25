import copy
import os
import shutil
import threading

import pytest

import mount_target_validator as mv


def make_dir(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def validator(tmp_path, **kwargs):
    kwargs.setdefault("allowed_source_roots", (str(tmp_path),))
    return mv.MountTargetValidator(**kwargs)


def resolved(path):
    return os.path.realpath(str(path))


def test_validate_returns_explicit_defaults_and_receipt(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = make_dir(tmp_path, "target")
    out = table.validate(str(source), str(target))
    assert out["ok"] is True
    assert out["recursive"] is False
    assert out["read_only"] is False
    assert out["would_create"] is False
    assert {"source_dev", "source_ino", "target_dev", "target_ino"} <= set(out["receipt"])
    assert table.mounts() == []
    assert table.stats() == {"validated": 1, "applied": 0, "unmounted": 0, "rejected": 0}


def test_validate_missing_source_rejected(tmp_path):
    table = validator(tmp_path)
    target = make_dir(tmp_path, "target")
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(tmp_path / "nope"), str(target))
    assert exc.value.code == "SOURCE_MISSING"
    assert table.stats()["rejected"] == 1
    assert table.stats()["validated"] == 0


def test_validate_source_outside_allowed_roots_rejected(tmp_path):
    root = make_dir(tmp_path, "root")
    source = make_dir(tmp_path, "elsewhere")
    target = make_dir(tmp_path, "target")
    table = mv.MountTargetValidator(allowed_source_roots=(str(root),))
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(source), str(target))
    assert exc.value.code == "SOURCE_OUTSIDE_ROOTS"
    assert table.mounts() == []


def test_validate_missing_target_requires_allow_create(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = tmp_path / "missing"
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(source), str(target))
    assert exc.value.code == "TARGET_MISSING"
    out = table.validate(str(source), str(target), allow_create=True)
    assert out["would_create"] is True
    assert target.exists() is False


def test_apply_creates_missing_target_only_with_allow_create(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = tmp_path / "created"
    record = table.apply(str(source), str(target), allow_create=True)
    assert target.is_dir()
    assert record["created_target"] is True
    assert record["source"] == resolved(source)
    assert record["target"] == resolved(target)
    assert table.is_mounted(str(target)) is True

    other = tmp_path / "not-created"
    with pytest.raises(mv.MountCheckError) as exc:
        table.apply(str(source), str(other))
    assert exc.value.code == "TARGET_MISSING"
    assert other.exists() is False


def test_self_bind_is_rejected(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "same")
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(source), str(source))
    assert exc.value.code == "SELF_BIND"
    assert table.mounts() == []
    assert table.stats()["rejected"] == 1


def test_overmount_requires_flag_and_records_covered_source(tmp_path):
    table = validator(tmp_path)
    first = make_dir(tmp_path, "first")
    second = make_dir(tmp_path, "second")
    target = make_dir(tmp_path, "target")
    table.apply(str(first), str(target))
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(second), str(target))
    assert exc.value.code == "OVERMOUNT_REQUIRED"

    record = table.apply(str(second), str(target), overmount=True)
    assert record["overmount"] is True
    assert record["covered_source"] == resolved(first)
    assert table.report()["overmounts"] == 1
    assert len(table.mounts()) == 2


def test_bind_unsupported_rejected_before_target_creation(tmp_path):
    table = validator(tmp_path, bind_supported=False)
    source = make_dir(tmp_path, "source")
    target = tmp_path / "should-not-exist"
    with pytest.raises(mv.MountCheckError) as exc:
        table.apply(str(source), str(target), allow_create=True)
    assert exc.value.code == "BIND_UNSUPPORTED"
    assert target.exists() is False
    assert table.mounts() == []
    assert table.is_mounted(str(target)) is False


def test_recursive_depth_cap(tmp_path):
    nested = tmp_path / "root" / "a" / "b"
    nested.mkdir(parents=True)
    flat = make_dir(tmp_path, "flat")
    target = make_dir(tmp_path, "target")
    table = validator(tmp_path, max_depth=1)
    plain = table.validate(str(nested), str(target))
    assert plain["recursive"] is False
    with pytest.raises(mv.MountCheckError) as exc:
        table.validate(str(nested), str(target), recursive=True)
    assert exc.value.code == "DEPTH_EXCEEDED"
    allowed = table.validate(str(flat), str(target), recursive=True)
    assert allowed["recursive"] is True


def test_capacity_cap_and_cleanup_of_created_target(tmp_path):
    table = validator(tmp_path, max_mounts=1)
    source = make_dir(tmp_path, "source")
    first = make_dir(tmp_path, "first")
    table.apply(str(source), str(first))
    second = tmp_path / "second"
    with pytest.raises(mv.MountCheckError) as exc:
        table.apply(str(source), str(second), allow_create=True)
    assert exc.value.code == "CAPACITY_EXCEEDED"
    assert second.exists() is False
    assert len(table.mounts()) == 1


def test_apply_rejects_target_inode_change_since_validate(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = make_dir(tmp_path, "target")
    receipt = table.validate(str(source), str(target))["receipt"]
    shutil.rmtree(target)
    target.mkdir()
    with pytest.raises(mv.MountCheckError) as exc:
        table.apply(str(source), str(target), receipt=receipt)
    assert exc.value.code == "TARGET_CHANGED"
    assert table.mounts() == []
    assert table.is_mounted(str(target)) is False


def test_concurrent_apply_same_target_only_one_wins(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = make_dir(tmp_path, "target")
    successes = []
    failures = []
    barrier = threading.Barrier(4)

    def worker():
        try:
            barrier.wait(timeout=5)
            table.apply(str(source), str(target))
            successes.append(1)
        except Exception as exc:  # reported through failures
            failures.append(repr(exc))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(successes) == 1
    assert failures == ["TARGET_BUSY", "TARGET_BUSY", "TARGET_BUSY"]
    assert len(table.mounts()) == 1


def test_unmount_lifo_reveals_covered_mount_and_unknown_rejected(tmp_path):
    table = validator(tmp_path)
    first = make_dir(tmp_path, "first")
    second = make_dir(tmp_path, "second")
    target = make_dir(tmp_path, "target")
    table.apply(str(first), str(target))
    table.apply(str(second), str(target), overmount=True)

    top = table.unmount(str(target))
    assert top["source"] == resolved(second)
    assert len(table.mounts()) == 1
    assert table.mounts()[0]["source"] == resolved(first)
    assert table.is_mounted(str(target)) is True
    table.unmount(str(target))
    assert table.is_mounted(str(target)) is False
    with pytest.raises(mv.MountCheckError) as exc:
        table.unmount(str(target))
    assert exc.value.code == "NOT_MOUNTED"
    with pytest.raises(mv.MountCheckError) as exc2:
        table.unmount(str(tmp_path / "elsewhere"))
    assert exc2.value.code == "NOT_MOUNTED"


def test_snapshot_restore_roundtrip_and_invalid_state(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = make_dir(tmp_path, "target")
    table.apply(str(source), str(target))
    state = table.snapshot()
    assert {"version", "mounts", "stats"} <= set(state)
    pristine = copy.deepcopy(state)
    state["mounts"].clear()
    state["stats"]["applied"] = 99
    assert len(table.mounts()) == 1
    assert table.stats()["applied"] == 1

    clone = validator(tmp_path)
    clone.restore(pristine)
    assert clone.mounts() == table.mounts()
    assert clone.stats() == table.stats()
    assert clone.is_mounted(str(target)) is True

    bad_states = [None, {}, {"version": 1, "mounts": [], "stats": {}},
                  {"version": 1, "mounts": {}, "stats": {}},
                  {"version": 1, "mounts": [], "stats": []}]
    for bad in bad_states:
        with pytest.raises(mv.MountCheckError) as exc:
            clone.restore(bad)
        assert exc.value.code == "STATE_INVALID"
    assert len(clone.mounts()) == 1


def test_stats_track_validates_applies_unmounts(tmp_path):
    table = validator(tmp_path)
    source = make_dir(tmp_path, "source")
    target = make_dir(tmp_path, "target")
    table.validate(str(source), str(target))
    table.apply(str(source), str(target))
    table.unmount(str(target))
    with pytest.raises(mv.MountCheckError):
        table.validate(str(source), str(tmp_path / "missing"))
    assert table.stats() == {"validated": 1, "applied": 1,
                             "unmounted": 1, "rejected": 1}
