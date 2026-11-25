import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

import ptp_slave_servo as ps


def build(**kwargs):
    params = {
        "window": 8,
        "step_threshold_ns": 1_000_000,
        "max_freq_ppb": 1000,
        "slew_divisor": 100,
        "max_steps": 4,
        "holdover_limit_ns": 10_000_000,
        "delay_age": 5,
    }
    params.update(kwargs)
    return ps.PtpSlaveServo(**params)


def servo_with_delay(delay_ns, **kwargs):
    servo = build(**kwargs)
    assert servo.feed_delay_req(1, 0, 2 * delay_ns) is True
    return servo


def add_offset(servo, seq, offset_ns, delay_ns):
    return servo.feed_sync(seq, 0, delay_ns + offset_ns)


def test_delay_request_computes_half_rtt_and_validates():
    servo = build()
    with pytest.raises(ps.PtpError) as info:
        servo.link_delay()
    assert info.value.code == "NO_DELAY"
    assert servo.feed_delay_req(1, 1000, 1600) is True
    assert servo.link_delay() == 300
    with pytest.raises(ps.PtpError) as info:
        servo.feed_delay_req(2, 1000, 900)
    assert info.value.code == "BAD_DELAY"
    assert servo.feed_delay_req(1, 0, 600) is False
    assert servo.link_delay() == 300


def test_sync_requires_a_valid_link_delay():
    servo = build()
    with pytest.raises(ps.PtpError) as info:
        servo.feed_sync(1, 0, 100)
    assert info.value.code == "NO_DELAY"


def test_offset_uses_link_delay():
    servo = servo_with_delay(300)
    assert add_offset(servo, 1, 200, 300) is True
    assert servo.offset_estimate() == 200
    assert servo.window_size() == 1


def test_offset_median_for_even_window():
    servo = servo_with_delay(0, window=4)
    for seq, offset in enumerate((10, 30, 20, 40), start=1):
        add_offset(servo, seq, offset, 0)
    assert servo.window_size() == 4
    assert servo.offset_estimate() == 25


def test_offset_window_trims_oldest_samples():
    servo = servo_with_delay(0, window=3)
    for seq, offset in enumerate((10, 20, 30, 40), start=1):
        add_offset(servo, seq, offset, 0)
    assert servo.window_size() == 3
    assert servo.offset_estimate() == 30


def test_out_of_order_and_duplicate_sequences_are_dropped():
    servo = servo_with_delay(0)
    assert add_offset(servo, 5, 5, 0) is True
    assert add_offset(servo, 5, 5, 0) is False
    assert add_offset(servo, 3, 3, 0) is False
    assert servo.window_size() == 1
    stats = servo.stats()
    assert stats["sync_samples"] == 1
    assert stats["dropped"] == 2


def test_servo_update_requires_samples():
    servo = build()
    with pytest.raises(ps.PtpError) as info:
        servo.offset_estimate()
    assert info.value.code == "NO_SAMPLES"
    with pytest.raises(ps.PtpError) as info:
        servo.servo_update()
    assert info.value.code == "NO_SAMPLES"


def test_slew_clamps_frequency_to_the_limit():
    servo = servo_with_delay(0)
    add_offset(servo, 1, 200_000, 0)
    result = servo.servo_update()
    assert result.mode == "slew"
    assert result.offset_ns == 200_000
    assert result.freq_ppb == 1000
    assert result.step_count == 0
    assert servo.frequency_ppb() == 1000


def test_slew_keeps_frequency_sign_and_floor_value():
    servo = servo_with_delay(0)
    add_offset(servo, 1, -500, 0)
    result = servo.servo_update()
    assert result.mode == "slew"
    assert result.freq_ppb == -5
    other = servo_with_delay(0)
    add_offset(other, 1, 250, 0)
    assert other.servo_update().freq_ppb == 2


def test_step_moves_the_local_clock():
    servo = servo_with_delay(300)
    add_offset(servo, 1, 2_000_000, 300)
    result = servo.servo_update()
    assert result.mode == "step"
    assert result.offset_ns == 2_000_000
    assert result.step_count == 1
    assert result.freq_ppb == 0
    assert servo.clock_ns() == 2_000_000


def test_step_limit_blocks_further_steps():
    servo = servo_with_delay(300, max_steps=1)
    add_offset(servo, 1, 2_000_000, 300)
    servo.servo_update()
    assert servo.clock_ns() == 2_000_000
    add_offset(servo, 2, 2_000_000, 300)
    with pytest.raises(ps.PtpError) as info:
        servo.servo_update()
    assert info.value.code == "STEP_LIMIT"
    assert servo.clock_ns() == 2_000_000
    assert servo.stats()["steps"] == 1


def test_step_threshold_is_strict_and_exposed():
    servo = servo_with_delay(0, step_threshold_ns=500_000)
    assert servo.step_threshold() == 500_000
    add_offset(servo, 1, 500_000, 0)
    assert servo.servo_update().mode == "slew"
    other = servo_with_delay(0, step_threshold_ns=500_000)
    add_offset(other, 1, 500_001, 0)
    assert other.servo_update().mode == "step"


def test_holdover_extrapolates_and_flags_unavailable():
    servo = servo_with_delay(0, holdover_limit_ns=1000)
    add_offset(servo, 1, 500, 0)
    servo.servo_update()
    within = servo.holdover(1_000_000_000)
    assert within.offset_ns == 505
    assert within.available is True
    assert within.elapsed_ns == 1_000_000_000
    beyond = servo.holdover(1_000_000_000_000)
    assert beyond.offset_ns == 5500
    assert beyond.available is False


def test_snapshot_restore_round_trip_and_invalid_state():
    servo = servo_with_delay(300, window=4)
    for seq, offset in enumerate((100, 200, 300), start=1):
        add_offset(servo, seq, offset, 300)
    servo.servo_update()
    assert servo.window_size() == 3
    assert servo.frequency_ppb() == 2
    snapshot = servo.snapshot()
    add_offset(servo, 4, 500, 300)
    servo.servo_update()
    assert servo.frequency_ppb() == 3
    servo.restore(snapshot)
    assert servo.window_size() == 3
    assert servo.offset_estimate() == 200
    assert servo.frequency_ppb() == 2
    assert servo.clock_ns() == 0
    with pytest.raises(ps.PtpError) as info:
        servo.restore({"samples": []})
    assert info.value.code == "SNAPSHOT_INVALID"
    assert servo.window_size() == 3


def test_concurrent_same_sequence_updates_window_once():
    servo = servo_with_delay(0)
    barrier = threading.Barrier(8)

    def feed(_):
        barrier.wait()
        return add_offset(servo, 7, 7, 0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(feed, range(8)))
    assert results.count(True) == 1
    assert results.count(False) == 7
    assert servo.window_size() == 1
    stats = servo.stats()
    assert stats["sync_samples"] == 1
    assert stats["dropped"] == 7
