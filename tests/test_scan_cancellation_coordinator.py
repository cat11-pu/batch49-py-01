import pytest

import scan_cancellation_coordinator as scc


def make_coordinator(**kwargs):
    return scc.ScanCancellationCoordinator(scc.CoordinatorConfig(**kwargs))


def test_register_and_state():
    coordinator = make_coordinator()
    node = coordinator.register("scan-1")
    assert node.state == scc.STATE_PENDING
    assert node.parent_id is None
    assert coordinator.state_of("scan-1") == scc.STATE_PENDING
    with pytest.raises(scc.UnknownTaskError):
        coordinator.state_of("scan-x")


def test_register_child_links_parent():
    coordinator = make_coordinator()
    coordinator.register("scan-p")
    coordinator.register("scan-c", parent_id="scan-p")
    assert coordinator.children_of("scan-p") == ("scan-c",)
    assert coordinator.children_of("scan-c") == ()


def test_register_duplicate_raises():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    with pytest.raises(scc.DuplicateTaskError):
        coordinator.register("scan-1")


def test_link_and_cycle_rejected():
    coordinator = make_coordinator()
    coordinator.register("a")
    coordinator.register("b")
    coordinator.register("c", parent_id="b")
    coordinator.link("b", "a")
    assert coordinator.children_of("a") == ("b",)
    assert coordinator.children_of("b") == ("c",)
    with pytest.raises(scc.CycleError):
        coordinator.link("a", "c")


def test_start_acquires_resources():
    coordinator = make_coordinator()
    coordinator.register("scan-1", target="host-01")
    node = coordinator.start("scan-1")
    assert node.state == scc.STATE_RUNNING
    assert coordinator.held_locks() == frozenset({"target:host-01"})
    assert coordinator.held_slots() == frozenset({"slot:scan-1"})


def test_complete_check_order_and_dedupe():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    coordinator.start("scan-1")
    coordinator.complete_check("scan-1", "auth")
    node = coordinator.complete_check("scan-1", "tls")
    coordinator.complete_check("scan-1", "auth")
    assert node.completed_checks == ("auth", "tls")


def test_finish_sets_terminal():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    coordinator.start("scan-1")
    node = coordinator.finish("scan-1")
    assert node.state == scc.STATE_COMPLETED
    assert coordinator.terminal_of("scan-1") == scc.REASON_COMPLETED


def test_cancel_cascades_to_children():
    coordinator = make_coordinator()
    coordinator.register("scan-p", target="host-01")
    coordinator.register("scan-c", parent_id="scan-p", target="host-02")
    coordinator.start("scan-p")
    coordinator.start("scan-c")
    report = coordinator.cancel("scan-p", "maintenance")
    assert report.cancelled == ("scan-p", "scan-c")
    assert coordinator.state_of("scan-p") == scc.STATE_CANCELLED
    assert coordinator.state_of("scan-c") == scc.STATE_CANCELLED


def test_cancel_releases_resources():
    coordinator = make_coordinator()
    coordinator.register("scan-p", target="host-01")
    coordinator.register("scan-c", parent_id="scan-p", target="host-02")
    coordinator.start("scan-p")
    coordinator.start("scan-c")
    coordinator.cancel("scan-p", "budget")
    assert coordinator.held_locks() == frozenset()
    assert coordinator.held_slots() == frozenset()


def test_cancel_records_reason():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    coordinator.start("scan-1")
    coordinator.cancel("scan-1", "policy-hit")
    assert coordinator.terminal_of("scan-1") == "policy-hit"


def test_cancel_child_keeps_parent_running():
    coordinator = make_coordinator()
    coordinator.register("scan-p")
    coordinator.register("scan-c", parent_id="scan-p")
    coordinator.start("scan-p")
    coordinator.start("scan-c")
    coordinator.cancel("scan-c", "false-positive")
    assert coordinator.state_of("scan-p") == scc.STATE_RUNNING
    assert coordinator.state_of("scan-c") == scc.STATE_CANCELLED


def test_deferred_cancel_requires_confirm():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    coordinator.start("scan-1")
    ticket = coordinator.request_cancel("scan-1", "operator")
    assert ticket.confirmed is False
    assert coordinator.state_of("scan-1") == scc.STATE_RUNNING
    report = coordinator.confirm_cancel(ticket)
    assert report.cancelled == ("scan-1",)
    assert coordinator.state_of("scan-1") == scc.STATE_CANCELLED


def test_seal_partial_covers_completed_checks():
    coordinator = make_coordinator()
    coordinator.register("scan-1")
    coordinator.start("scan-1")
    coordinator.complete_check("scan-1", "headers")
    coordinator.complete_check("scan-1", "tls")
    coordinator.cancel("scan-1", "timeout")
    sealed = coordinator.seal_partial("scan-1")
    assert sealed.task_id == "scan-1"
    assert sealed.covered_checks == ("headers", "tls")
    assert sealed.reason == "timeout"


def test_snapshot_restore_round_trip():
    coordinator = make_coordinator()
    coordinator.register("scan-p", target="host-01")
    coordinator.register("scan-c", parent_id="scan-p")
    coordinator.start("scan-p")
    coordinator.complete_check("scan-p", "auth")
    snapshot = coordinator.snapshot()
    other = make_coordinator()
    other.restore(snapshot)
    assert other.snapshot() == snapshot
    assert other.state_of("scan-p") == scc.STATE_RUNNING
    assert other.children_of("scan-p") == ("scan-c",)


def test_stats_counts_states():
    coordinator = make_coordinator()
    coordinator.register("scan-p")
    coordinator.register("scan-c1", parent_id="scan-p")
    coordinator.register("scan-c2", parent_id="scan-p")
    coordinator.start("scan-p")
    coordinator.start("scan-c1")
    coordinator.finish("scan-c1")
    coordinator.cancel("scan-c2", "dup")
    assert coordinator.stats() == {
        "pending": 0,
        "running": 1,
        "completed": 1,
        "cancelled": 1,
    }
