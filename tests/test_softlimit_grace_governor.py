import threading

import pytest

import softlimit_grace_governor as gov


def make(grace=30, hysteresis=60, soft=100, hard=200):
    g = gov.SoftlimitGraceGovernor(grace_seconds=grace, hysteresis_seconds=hysteresis)
    g.define("disk", soft, hard)
    return g


def test_initial_usage_is_zero_and_state_normal():
    g = make()
    assert g.observe("disk") == 0
    assert g.state("disk", now=0) == "normal"


def test_duplicate_category_is_rejected():
    g = make()
    with pytest.raises(gov.LimitError) as err:
        g.define("disk", 10, 20)
    assert err.value.code == "duplicate_category"


def test_admit_below_soft_limit_is_plain_allow():
    g = make()
    decision = g.admit("disk", 50, now=0)
    assert decision["allowed"] is True
    assert decision["throttled"] is False
    assert decision["usage"] == 50
    assert g.observe("disk") == 50
    assert g.state("disk", now=0) == "normal"


def test_crossing_soft_limit_enters_throttled_and_alerts_once():
    g = make()
    decision = g.admit("disk", 120, now=0)
    assert decision["allowed"] is True
    assert decision["throttled"] is True
    assert g.state("disk", now=0) == "throttled"
    assert g.alert_count("disk") == 1


def test_second_crossing_inside_grace_window_does_not_alert_again():
    g = make()
    g.admit("disk", 120, now=0)
    g.admit("disk", 10, now=5)
    assert g.alert_count("disk") == 1
    g.admit("disk", 10, now=100)
    assert g.alert_count("disk") == 2


def test_over_hard_limit_rejects_without_writing_usage():
    g = make()
    g.admit("disk", 120, now=0)
    decision = g.admit("disk", 100, now=10)
    assert decision["allowed"] is False
    assert decision["reason"] == "hard_limit"
    assert g.observe("disk") == 120
    assert g.hard_rejections("disk") == 1


def test_hard_and_soft_counters_are_separate():
    g = make()
    g.admit("disk", 120, now=0)
    g.admit("disk", 50, now=100)
    g.admit("disk", 100, now=200)
    assert g.soft_throttles("disk") == 2
    assert g.hard_rejections("disk") == 1


def test_unknown_category_is_rejected_instead_of_default_quota():
    g = make()
    with pytest.raises(gov.LimitError) as err:
        g.admit("gpu", 1, now=0)
    assert err.value.code == "unknown_category"


def test_negative_amount_is_rejected_and_usage_untouched():
    g = make()
    g.admit("disk", 50, now=0)
    with pytest.raises(gov.LimitError) as err:
        g.admit("disk", -5, now=1)
    assert err.value.code == "bad_amount"
    assert g.observe("disk") == 50


def test_release_reduces_usage():
    g = make()
    g.admit("disk", 120, now=0)
    assert g.release("disk", 40, now=10) == 80
    assert g.observe("disk") == 80


def test_below_soft_but_before_hysteresis_stays_throttled():
    g = make()
    g.admit("disk", 120, now=0)
    g.release("disk", 40, now=10)
    assert g.state("disk", now=50) == "throttled"


def test_after_hysteresis_state_returns_normal():
    g = make()
    g.admit("disk", 120, now=0)
    g.release("disk", 40, now=10)
    assert g.state("disk", now=80) == "normal"


def test_snapshot_restore_keeps_usage_state_and_alert_window():
    g = make()
    g.admit("disk", 120, now=0)
    snap = g.snapshot()
    g2 = make()
    g2.restore(snap)
    assert g2.observe("disk") == 120
    assert g2.state("disk", now=0) == "throttled"
    assert g2.alert_count("disk") == 1
    g2.admit("disk", 10, now=5)
    assert g2.alert_count("disk") == 1


def test_alert_callback_failure_does_not_block_admission():
    g = make()
    seen = []

    def boom(category, decision):
        seen.append(category)
        raise RuntimeError("alert channel down")

    g.on_alert(boom)
    decision = g.admit("disk", 120, now=0)
    assert decision["allowed"] is True
    assert seen == ["disk"]
    assert g.alert_count("disk") == 1


def test_concurrent_admits_never_oversell():
    g = gov.SoftlimitGraceGovernor()
    g.define("disk", 100, 105)
    barrier = threading.Barrier(20)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        d = g.admit("disk", 10, now=0)
        with lock:
            results.append(d["allowed"])

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(1 for ok in results if ok) == 10
    assert g.observe("disk") == 100
