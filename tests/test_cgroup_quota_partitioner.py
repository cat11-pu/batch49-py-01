import threading

import pytest

import cgroup_quota_partitioner as cqp


def partitioner(**kwargs):
    return cqp.CgroupQuotaPartitioner(**kwargs)


def tree():
    part = partitioner()
    part.create_root("root", 100)
    return part


def test_create_child_draws_from_parent_available():
    part = tree()
    child = part.create_child("root", "a", 40)
    assert (child.path, child.parent, child.depth) == ("root/a", "root", 1)
    assert part.available("root") == 60
    assert part.allocated("root") == 40
    assert part.available("root/a") == 40
    assert tuple(part.children("root")) == ("root/a",)


def test_child_over_parent_available_is_refused():
    part = tree()
    part.create_child("root", "a", 60)
    with pytest.raises(cqp.QuotaError) as exc:
        part.create_child("root", "b", 50)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.detail["available"] == 40
    assert tuple(part.children("root")) == ("root/a",)
    assert part.available("root") == 40


def test_shrink_below_allocated_is_refused():
    part = tree()
    part.create_child("root", "a", 40)
    with pytest.raises(cqp.QuotaError) as exc:
        part.resize("root", 30)
    assert exc.value.code == "QUOTA_SHRINK_REFUSED"
    assert exc.value.detail["allocated"] == 40
    assert part.node("root").quota == 100


def test_parent_growth_only_moves_the_free_remainder():
    part = tree()
    part.create_child("root", "a", 40)
    grown = part.resize("root", 150, request_id="r-grow", operator="ops")
    assert grown.quota == 150
    assert part.available("root") == 110
    assert part.node("root/a").quota == 40


def test_release_returns_quota_and_is_idempotent():
    part = tree()
    part.create_child("root", "a", 40)
    assert part.release("root/a") == 40
    assert part.available("root") == 100
    assert tuple(part.children("root")) == ()
    assert part.release("root/a") == 0
    assert part.available("root") == 100


def test_releasing_a_parent_with_children_is_refused():
    part = tree()
    part.create_child("root", "a", 40)
    part.create_child("root/a", "b", 20)
    with pytest.raises(cqp.QuotaError) as exc:
        part.release("root/a")
    assert exc.value.code == "QUOTA_HAS_CHILDREN"
    assert tuple(part.children("root/a")) == ("root/a/b",)


def test_migrate_moves_quota_between_parents():
    part = partitioner()
    part.create_root("p1", 100)
    part.create_root("p2", 100)
    part.create_child("p1", "a", 40)
    moved = part.migrate("p1/a", "p2", request_id="m1", operator="ops")
    assert moved.path == "p2/a"
    assert moved.parent == "p2"
    assert part.available("p1") == 100
    assert part.available("p2") == 60
    with pytest.raises(cqp.QuotaError) as exc:
        part.node("p1/a")
    assert exc.value.code == "QUOTA_UNKNOWN_NODE"


def test_migrate_into_a_full_parent_changes_nothing():
    part = partitioner()
    part.create_root("p1", 100)
    part.create_root("p2", 100)
    part.create_child("p1", "a", 40)
    part.create_child("p2", "b", 70)
    with pytest.raises(cqp.QuotaError) as exc:
        part.migrate("p1/a", "p2")
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert part.node("p1/a").parent == "p1"
    assert part.available("p1") == 60
    assert part.available("p2") == 30


def test_negative_weights_are_refused_and_zero_weight_is_inert():
    part = tree()
    with pytest.raises(cqp.QuotaError) as exc:
        part.create_child("root", "bad", 10, weight=-1)
    assert exc.value.code == "QUOTA_BAD_WEIGHT"
    part.create_child("root", "idle", 10, weight=0)
    part.create_child("root", "hot", 10, weight=100)
    granted = part.arbitrate("root", {"idle": 50, "hot": 80})
    assert granted == {"idle": 0, "hot": 80}
    assert part.node("root/idle").weight == 0


def test_depth_limit_applies_to_deep_creation():
    part = partitioner(max_depth=2)
    part.create_root("root", 100)
    part.create_child("root", "a", 80)
    part.create_child("root/a", "b", 60)
    with pytest.raises(cqp.QuotaError) as exc:
        part.create_child("root/a/b", "c", 10)
    assert exc.value.code == "QUOTA_DEPTH_LIMIT"
    assert part.node("root/a/b").depth == 2


def test_child_count_limit_applies_per_node():
    part = partitioner(max_children=2)
    part.create_root("root", 100)
    part.create_child("root", "a", 10)
    part.create_child("root", "b", 10)
    with pytest.raises(cqp.QuotaError) as exc:
        part.create_child("root", "c", 10)
    assert exc.value.code == "QUOTA_CHILD_LIMIT"
    assert exc.value.detail["limit"] == 2


def test_deep_creation_fails_when_an_ancestor_level_is_exhausted():
    part = tree()
    part.create_child("root", "a", 100)
    part.create_child("root/a", "b", 60)
    with pytest.raises(cqp.QuotaError) as exc:
        part.create_child("root/a", "c", 50)
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.detail["available"] == 40
    assert part.children("root/a") == ("root/a/b",)


def test_journal_records_actor_and_replays_one_request_once():
    part = tree()
    first = part.create_child("root", "a", 40, operator="alice", request_id="r1")
    replay = part.create_child("root", "a", 40, operator="alice", request_id="r1")
    assert first == replay
    assert part.children("root") == ("root/a",)
    entries = part.journal()
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.request_id, entry.operator, entry.action) == ("r1", "alice",
                                                                "create_child")
    assert (entry.path, entry.before, entry.after) == ("root/a", 0, 40)
    part.resize("root/a", 55, operator="bob", request_id="r2")
    assert part.journal()[-1].before == 40
    assert part.journal()[-1].after == 55


def test_snapshot_roundtrip_and_inconsistent_restore_is_refused():
    part = tree()
    part.create_child("root", "a", 40)
    part.create_child("root/a", "b", 15)
    state = part.snapshot()

    revived = partitioner()
    revived.restore(state)
    assert revived.available("root") == 60
    assert revived.children("root/a") == ("root/a/b",)
    assert len(revived.journal()) == len(part.journal())

    tampered = part.snapshot()
    for row in tampered["nodes"]:
        if row["path"] == "root/a":
            row["quota"] = 200
    with pytest.raises(cqp.QuotaError) as exc:
        part.restore(tampered)
    assert exc.value.code == "QUOTA_SNAPSHOT_INCONSISTENT"
    assert part.node("root/a").quota == 40


def test_concurrent_split_never_oversubscribes_a_parent():
    part = tree()
    accepted = []
    rejected = []
    lock = threading.Lock()

    def worker(index):
        try:
            part.create_child("root", "c%d" % index, 20,
                              operator="ops", request_id="req-%d" % index)
            with lock:
                accepted.append(index)
        except cqp.QuotaError as exc:
            with lock:
                rejected.append(exc.code)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(accepted) == 5
    assert rejected == ["QUOTA_EXCEEDED"]
    assert part.available("root") == 0
    assert len(part.children("root")) == 5
