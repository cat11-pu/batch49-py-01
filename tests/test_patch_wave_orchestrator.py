import pytest

import patch_wave_orchestrator as po


def make(max_waves=8):
    log = []
    orchestrator = po.PatchWaveOrchestrator(
        lambda asset_id: log.append(("apply", asset_id)),
        lambda asset_id: log.append(("revert", asset_id)),
        max_waves=max_waves,
    )
    return orchestrator, log


def asset(asset_id, group, patch="p-1"):
    return po.Asset(asset_id=asset_id, group=group, patch=patch)


def healthy(index):
    return {"ok": True, "probe": index}


def unhealthy(index):
    return {"ok": False, "probe": index}


def test_plan_groups_assets_by_first_seen_group():
    orchestrator, _ = make()
    waves = orchestrator.plan(
        [
            asset("web-2", "web"),
            asset("web-1", "web"),
            asset("db-1", "db"),
        ]
    )
    assert [(wave.index, wave.group, wave.status, wave.applied) for wave in waves] == [
        (0, "web", "pending", ()),
        (1, "db", "pending", ()),
    ]
    assert orchestrator.pending() == ["web-1", "web-2", "db-1"]


def test_plan_rejects_duplicate_asset_ids():
    orchestrator, _ = make()
    with pytest.raises(po.PatchWaveError) as exc:
        orchestrator.plan([asset("web-1", "web"), asset("web-1", "db")])
    assert exc.value.code == "ASSET_DUPLICATE"
    assert orchestrator.status()["total"] == 0


def test_run_next_applies_wave_and_records_health():
    orchestrator, log = make()
    orchestrator.plan([asset("web-2", "web"), asset("web-1", "web")])
    result = orchestrator.run_next(healthy)
    assert result.index == 0
    assert result.status == "applied"
    assert result.applied == ("web-1", "web-2")
    assert result.health == {"ok": True, "probe": 0}
    assert log == [("apply", "web-1"), ("apply", "web-2")]
    assert orchestrator.status()["counts"]["applied"] == 1


def test_failed_health_blocks_next_wave():
    orchestrator, _ = make()
    orchestrator.plan([asset("web-1", "web"), asset("db-1", "db")])
    result = orchestrator.run_next(unhealthy)
    assert result.status == "failed"
    assert orchestrator.status()["counts"]["failed"] == 1
    with pytest.raises(po.PatchWaveError) as exc:
        orchestrator.run_next(healthy)
    assert exc.value.code == "WAVE_BLOCKED"
    assert orchestrator.status()["counts"]["pending"] == 1


def test_pause_blocks_run_next_until_resume():
    orchestrator, _ = make()
    orchestrator.plan([asset("web-1", "web")])
    orchestrator.pause()
    assert orchestrator.status()["paused"] is True
    with pytest.raises(po.PatchWaveError) as exc:
        orchestrator.run_next(healthy)
    assert exc.value.code == "ORCHESTRATOR_PAUSED"
    orchestrator.resume()
    assert orchestrator.status()["paused"] is False
    assert orchestrator.run_next(healthy).status == "applied"


def test_run_all_stops_at_first_failed_wave():
    orchestrator, _ = make()
    orchestrator.plan(
        [asset("web-1", "web"), asset("db-1", "db"), asset("cache-1", "cache")]
    )

    def gated(index):
        return {"ok": index < 1}

    results = orchestrator.run_all(gated)
    assert [(result.index, result.status) for result in results] == [
        (0, "applied"),
        (1, "failed"),
    ]
    assert orchestrator.status()["counts"]["pending"] == 1
    assert "cache-1" in orchestrator.pending()


def test_rollback_failed_wave_reverts_assets_in_reverse_order():
    orchestrator, log = make()
    orchestrator.plan([asset("web-1", "web"), asset("web-2", "web")])
    orchestrator.run_next(unhealthy)
    reverted = orchestrator.rollback()
    assert reverted == ("web-2", "web-1")
    assert log[-2:] == [("revert", "web-2"), ("revert", "web-1")]
    assert orchestrator.status()["counts"]["rolled_back"] == 1
    assert orchestrator.applied_order() == []


def test_rollback_failed_wave_keeps_completed_waves_applied():
    orchestrator, _ = make()
    orchestrator.plan([asset("web-1", "web"), asset("db-1", "db")])
    assert orchestrator.run_next(healthy).status == "applied"
    assert orchestrator.run_next(unhealthy).status == "failed"

    orchestrator.rollback()
    assert orchestrator.applied_order() == ["web-1"]
    assert orchestrator.status()["counts"]["applied"] == 1
    assert orchestrator.status()["counts"]["rolled_back"] == 1
    assert orchestrator.pending() == []

    assert orchestrator.run_next(healthy).applied == ("db-1",)
    assert orchestrator.applied_order() == ["web-1", "db-1"]


def test_rollback_without_argument_targets_last_applied_wave():
    orchestrator, _ = make()
    orchestrator.plan([asset("web-1", "web"), asset("db-1", "db")])
    orchestrator.run_all(healthy)
    reverted = orchestrator.rollback()
    assert reverted == ("db-1",)
    assert orchestrator.applied_order() == ["web-1"]
    counts = orchestrator.status()["counts"]
    assert counts["applied"] == 1
    assert counts["rolled_back"] == 1


def test_replay_of_failed_wave_does_not_reapply_applied_assets():
    orchestrator, log = make()
    orchestrator.plan([asset("web-1", "web"), asset("web-2", "web")])
    orchestrator.run_next(unhealthy)
    before = [entry for entry in log if entry[0] == "apply"]

    result = orchestrator.replay(0)
    assert result.status == "applied"
    assert [entry for entry in log if entry[0] == "apply"] == before
    assert orchestrator.applied_order() == ["web-1", "web-2"]
    assert orchestrator.status()["counts"]["applied"] == 1


def test_wave_limit_rejects_oversized_plan():
    orchestrator, _ = make(max_waves=2)
    with pytest.raises(po.PatchWaveError) as exc:
        orchestrator.plan(
            [asset("a-1", "a"), asset("b-1", "b"), asset("c-1", "c")]
        )
    assert exc.value.code == "WAVE_LIMIT_EXCEEDED"
    assert orchestrator.status()["total"] == 0


def test_status_counts_waves_by_state():
    orchestrator, _ = make()
    orchestrator.plan(
        [asset("web-1", "web"), asset("db-1", "db"), asset("cache-1", "cache")]
    )

    def gated(index):
        return {"ok": index < 1}

    orchestrator.run_all(gated)
    state = orchestrator.status()
    assert state["total"] == 3
    assert state["paused"] is False
    assert state["counts"] == {
        "pending": 1,
        "applied": 1,
        "failed": 1,
        "rolled_back": 0,
    }


def test_applied_order_follows_application_sequence():
    orchestrator, _ = make()
    orchestrator.plan(
        [asset("web-2", "web"), asset("web-1", "web"), asset("db-1", "db")]
    )
    orchestrator.run_all(healthy)
    assert orchestrator.applied_order() == ["web-1", "web-2", "db-1"]
    assert orchestrator.pending() == []


def test_snapshot_detached_and_restore_continues():
    orchestrator, log = make()
    orchestrator.plan([asset("web-1", "web"), asset("db-1", "db")])
    orchestrator.run_next(healthy)
    state = orchestrator.snapshot()
    assert isinstance(state, dict) and state

    clone, _ = make()
    clone.restore(state)
    assert clone.status()["counts"]["applied"] == 1
    result = clone.run_next(healthy)
    assert result.applied == ("db-1",)
    assert clone.applied_order() == ["web-1", "db-1"]
    assert orchestrator.status()["counts"]["applied"] == 1
    assert ("apply", "web-1") in log


def test_restore_rejects_malformed_state():
    orchestrator, _ = make()
    orchestrator.plan([asset("web-1", "web")])
    orchestrator.run_next(healthy)
    for bad_state in (None, {}, {"waves": "nope"}):
        with pytest.raises(po.PatchWaveError) as exc:
            orchestrator.restore(bad_state)
        assert exc.value.code == "SNAPSHOT_INVALID"
    assert orchestrator.status()["counts"]["applied"] == 1
    assert orchestrator.applied_order() == ["web-1"]
