import errno
import threading

import pytest

import namespace_create_rollback as ns


def make_creator(start=3, fail_on=None, err=errno.EPERM):
    state = {"next": start, "calls": []}

    def creator(kind):
        state["calls"].append(kind)
        if kind == fail_on:
            raise OSError(err, "creator failed")
        fd = state["next"]
        state["next"] += 1
        return fd

    creator.calls = state["calls"]
    return creator, state


def make_closer():
    calls = []

    def closer(fd):
        calls.append(fd)

    closer.calls = calls
    return closer


def rollback(creator=None, closer=None, registrar=None, **kwargs):
    if creator is None:
        creator, _ = make_creator()
    if closer is None:
        closer = make_closer()
    return ns.NamespaceCreateRollback(creator=creator, closer=closer,
                                      registrar=registrar, **kwargs)


ALL_FDS = {"user": 3, "pid": 4, "net": 5, "mnt": 6, "ipc": 7, "uts": 8}


def test_create_all_namespaces_in_fixed_order():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    out = rb.create("u1")
    assert out["unit"] == "u1"
    assert out["kinds"] == ("user", "pid", "net", "mnt", "ipc", "uts")
    assert out["fds"] == ALL_FDS
    assert out["shared"] is False
    assert creator.calls == ["user", "pid", "net", "mnt", "ipc", "uts"]
    assert rb.fd_table() == {"u1": ALL_FDS}
    records = rb.list()
    assert len(records) == 1
    assert records[0]["unit"] == "u1"
    assert records[0]["fds"] == ALL_FDS
    assert records[0]["refs"]["user"] == 1


def test_create_only_requested_kinds():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    out = rb.create("u1", kinds=["net", "user"])
    assert out["kinds"] == ("user", "net")
    assert out["fds"] == {"user": 3, "net": 4}
    assert creator.calls == ["user", "net"]


def test_kinds_without_user_are_refused_before_creation():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1", kinds=["pid", "net"])
    assert excinfo.value.code == "NS_USER_REQUIRED"
    assert creator.calls == []
    assert rb.stats()["open_fds"] == 0


def test_failure_midway_closes_previous_fds_in_reverse():
    creator, state = make_creator(fail_on="net", err=errno.EPERM)
    closer = make_closer()
    rb = rollback(creator=creator, closer=closer)
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1")
    assert excinfo.value.code == "NS_CREATE_FAILED"
    assert excinfo.value.detail == {"kind": "net", "errno": errno.EPERM,
                                    "closed": [4, 3]}
    assert closer.calls == [4, 3]
    log = rb.rollback_log()
    assert [entry["action"] for entry in log] == [
        "close_intent", "closed", "close_intent", "closed"]
    assert [entry["fd"] for entry in log] == [4, 4, 3, 3]
    assert rb.stats()["rolled_back"] == 2
    assert rb.stats()["open_fds"] == 0


def test_registration_failure_closes_every_fd():
    creator, state = make_creator()
    closer = make_closer()

    def registrar(unit, fds):
        raise RuntimeError("registry down")

    rb = rollback(creator=creator, closer=closer, registrar=registrar)
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1")
    assert excinfo.value.code == "NS_REGISTRY_FAILED"
    assert closer.calls == [8, 7, 6, 5, 4, 3]
    assert rb.stats()["rolled_back"] == 6
    assert rb.stats()["open_fds"] == 0


def test_exclusive_unit_name_cannot_be_reused():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    rb.create("u1", kinds=["user"])
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1", kinds=["user"])
    assert excinfo.value.code == "NS_EXISTS"
    assert creator.calls == ["user"]


def test_shared_key_reuses_namespace_and_counts_refs():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    first = rb.create("u1", kinds=["user", "net"], share_key="k",
                      exclusive=False)
    second = rb.create("u1", kinds=["user", "net"], share_key="k",
                       exclusive=False)
    assert first["fds"] == {"user": 3, "net": 4}
    assert second["fds"] == first["fds"]
    assert second["shared"] is True
    assert rb.refcount("u1", "user") == 2
    assert rb.stats()["shared_hits"] == 1
    assert creator.calls == ["user", "net"]


def test_shared_release_closes_only_at_zero_and_is_idempotent():
    creator, state = make_creator()
    closer = make_closer()
    rb = rollback(creator=creator, closer=closer)
    rb.create("u1", kinds=["user", "net"], share_key="k", exclusive=False)
    rb.create("u1", kinds=["user", "net"], share_key="k", exclusive=False)
    assert rb.release("u1") == 0
    assert rb.refcount("u1", "user") == 1
    assert closer.calls == []
    assert rb.release("u1") == 2
    assert rb.refcount("u1", "user") == 0
    assert closer.calls == [4, 3]
    assert rb.release("u1") == 0
    assert closer.calls == [4, 3]


def test_namespace_count_per_unit_is_capped():
    creator, state = make_creator()
    rb = rollback(creator=creator, max_ns_per_unit=2)
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1")
    assert excinfo.value.code == "NS_LIMIT"
    assert creator.calls == []


def test_global_fd_budget_is_enforced_before_creation():
    creator, state = make_creator()
    rb = rollback(creator=creator, max_fds=4)
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.create("u1")
    assert excinfo.value.code == "FD_EXHAUSTED"
    assert creator.calls == []
    assert rb.rollback_log() == []


def test_concurrent_create_of_same_unit_has_single_winner():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    results = []
    barrier = threading.Barrier(2)

    def run():
        barrier.wait()
        try:
            out = rb.create("u1")
            results.append(("ok", out["fds"]))
        except ns.NamespaceError as exc:
            results.append(("err", exc.code))
        except Exception as exc:  # noqa: BLE001 - reported for diagnosis
            results.append(("boom", type(exc).__name__))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)
    assert sorted(item[0] for item in results) == ["err", "ok"]
    assert [item[1] for item in results if item[0] == "err"] == ["NS_EXISTS"]
    winner_fds = [item[1] for item in results if item[0] == "ok"][0]
    assert winner_fds == ALL_FDS
    assert creator.calls == ["user", "pid", "net", "mnt", "ipc", "uts"]


def test_released_unit_can_be_created_again_with_new_fds():
    creator, state = make_creator()
    closer = make_closer()
    rb = rollback(creator=creator, closer=closer)
    rb.create("u1", kinds=["user"])
    assert rb.release("u1") == 1
    assert closer.calls == [3]
    assert rb.release("u1") == 0
    again = rb.create("u1", kinds=["user"])
    assert again["fds"] == {"user": 4}
    assert rb.refcount("u1", "user") == 1


def test_snapshot_restore_keeps_shared_counts_and_fds():
    creator, state = make_creator()
    rb = rollback(creator=creator)
    rb.create("u1", kinds=["user", "net"], share_key="k", exclusive=False)
    rb.create("u1", kinds=["user", "net"], share_key="k", exclusive=False)
    snap = rb.snapshot()

    other_creator, _ = make_creator()
    other = rollback(creator=other_creator)
    other.restore(snap)
    assert other.refcount("u1", "user") == 2
    assert other.fd_table() == {"u1": {"user": 3, "net": 4}}
    assert other.stats()["open_fds"] == 2
    assert other.stats()["units"] == 1


def test_invalid_snapshot_is_rejected():
    rb = rollback()
    with pytest.raises(ns.NamespaceError) as excinfo:
        rb.restore({"units": {}})
    assert excinfo.value.code == "SNAPSHOT_INVALID"
    assert rb.stats()["units"] == 0


def test_stats_track_creation_and_release():
    creator, state = make_creator()
    closer = make_closer()
    rb = rollback(creator=creator, closer=closer)
    rb.create("u1")
    assert rb.release("u1") == 6
    assert closer.calls == [8, 7, 6, 5, 4, 3]
    assert rb.stats() == {"created": 6, "closed": 6, "rolled_back": 0,
                          "shared_hits": 0, "units": 0, "open_fds": 0,
                          "close_errors": 0}
