import os

import pytest

import scan_shard_resumer as ssr

CONFIG = ssr.ShardPlanConfig(max_shard_size=100, min_shard_size=10, max_shards=8)
TARGETS = [f"host-{i:03d}" for i in range(250)]


def make_resumer(config=None):
    return ssr.ScanShardResumer(config or CONFIG)


def test_plan_splits_by_max_shard_size():
    shards = make_resumer().plan(TARGETS)
    assert [shard.index for shard in shards] == [1, 2, 3]
    assert [len(shard.targets) for shard in shards] == [100, 100, 50]
    assert shards[0].targets[0] == "host-000"
    assert shards[2].targets[-1] == "host-249"


def test_plan_empty_targets():
    assert make_resumer().plan([]) == ()


def test_plan_rejects_too_many_shards():
    config = ssr.ShardPlanConfig(max_shard_size=2, min_shard_size=1, max_shards=3)
    with pytest.raises(ssr.ShardLimitExceeded):
        make_resumer(config).plan([f"h{i}" for i in range(10)])


def test_plan_rejects_duplicate_targets():
    resumer = make_resumer()
    with pytest.raises(ssr.DuplicateTargetError):
        resumer.plan(["a.example", "b.example", "a.example"])


def test_shard_of_target():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    assert resumer.shard_of_target("host-000") == 1
    assert resumer.shard_of_target("host-150") == 2
    assert resumer.shard_of_target("host-249") == 3
    assert resumer.shard_of_target("host-999") == -1


def test_mark_started_then_completed():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    assert resumer.mark_started(1) is True
    assert resumer.pending_shards() == (2, 3)
    resumer.mark_completed(1, 100)
    assert resumer.completed_shards() == (1,)
    assert resumer.pending_shards() == (2, 3)


def test_mark_failed_records_reason():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_failed(3, "timeout")
    assert resumer.failed_shards() == {3: "timeout"}
    assert resumer.pending_shards() == (1, 2)
    assert resumer.completed_shards() == ()


def test_claim_and_release_owner():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    first = resumer.claim(2, "worker-a")
    assert first.granted is True
    assert first.holder == "worker-a"
    resumer.release(2, "worker-a")
    second = resumer.claim(2, "worker-b")
    assert second.granted is True
    assert second.holder == "worker-b"


def test_save_and_load_round_trip(tmp_path):
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_completed(1, 100)
    path = str(tmp_path / "ledger.json")
    resumer.save(path)
    loaded = make_resumer().load(path)
    assert len(loaded.shards) == 3
    assert loaded.shards[1].state == ssr.STATE_COMPLETED
    assert loaded.shards[2].state == ssr.STATE_PENDING


def test_save_leaves_no_partial_files(tmp_path):
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.save(str(tmp_path / "ledger.json"))
    assert sorted(os.listdir(tmp_path)) == ["ledger.json"]


def test_resume_skips_completed_shards():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_completed(1, 100)
    resumer.mark_completed(2, 100)
    report = resumer.resume(TARGETS)
    assert report.resumed_completed == (1, 2)
    assert report.resumed_pending == (3,)


def test_resume_keeps_failed_shard_pending():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_failed(2, "connect-refused")
    report = resumer.resume(TARGETS)
    assert report.resumed_pending == (1, 2, 3)
    assert report.resumed_completed == ()


def test_cancel_keeps_completed_progress():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_completed(1, 100)
    resumer.mark_started(2)
    resumer.cancel()
    assert resumer.completed_shards() == (1,)
    assert resumer.pending_shards() == ()


def test_snapshot_restore_round_trip():
    resumer = make_resumer()
    resumer.plan(TARGETS)
    resumer.mark_completed(1, 100)
    snapshot = resumer.snapshot()
    other = make_resumer()
    other.restore(snapshot)
    assert other.snapshot() == snapshot


def test_refine_splits_failed_shard_into_new_indices():
    resumer = make_resumer()
    resumer.plan([f"host-{i:03d}" for i in range(200)])
    resumer.mark_failed(1, "boom")
    new_shards = resumer.refine(1)
    assert [shard.index for shard in new_shards] == [3, 4]
    assert [len(shard.targets) for shard in new_shards] == [50, 50]
    assert resumer.pending_shards() == (2, 3, 4)
    assert resumer.failed_shards() == {1: "boom"}
