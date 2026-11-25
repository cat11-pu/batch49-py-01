import copy
import math
import threading

import pytest

import pan_law_mixer as plm


def mixer(**kwargs):
    return plm.PanLawMixer(**kwargs)


def add(instance, source_id, *, pan=0.0, gain=1.0):
    return instance.add_source(source_id, pan=pan, gain=gain)


def test_add_source_reports_state_and_initial_stats():
    m = mixer(max_sources=4)
    info = add(m, "s1", pan=0.5, gain=0.25)
    assert info["source_id"] == "s1"
    assert info["pan"] == pytest.approx(0.5)
    assert info["gain"] == pytest.approx(0.25)
    assert info["pan_law"] == "linear"
    assert m.sources() == ["s1"]
    assert m.stats() == {"sources": 1, "rendered_frames": 0, "law_switches": 0,
                         "ramps_started": 0, "removed": 0, "rejected": 0}


def test_duplicate_and_unknown_source_use_stable_codes():
    m = mixer()
    add(m, "s1")
    with pytest.raises(plm.PanLawError) as exc:
        add(m, "s1")
    assert exc.value.code == "PAN_SOURCE_EXISTS"
    assert m.stats()["rejected"] == 1

    with pytest.raises(plm.PanLawError) as missing_pan:
        m.set_pan("ghost", 0.5)
    assert missing_pan.value.code == "PAN_SOURCE_MISSING"

    with pytest.raises(plm.PanLawError) as missing_remove:
        m.remove_source("ghost")
    assert missing_remove.value.code == "PAN_SOURCE_MISSING"


def test_capacity_pan_range_and_frame_validation():
    m = mixer(max_sources=2)
    add(m, "s1")
    add(m, "s2")
    with pytest.raises(plm.PanLawError) as full:
        add(m, "s3")
    assert full.value.code == "PAN_CAPACITY_EXCEEDED"

    with pytest.raises(plm.PanLawError) as over:
        m.set_pan("s1", 1.5)
    assert over.value.code == "PAN_RANGE_INVALID"

    with pytest.raises(plm.PanLawError) as negative:
        m.set_gain("s1", -0.1)
    assert negative.value.code == "PAN_RANGE_INVALID"

    with pytest.raises(plm.PanLawError) as bad_frames:
        m.render(-1)
    assert bad_frames.value.code == "PAN_FRAME_INVALID"

    assert m.render(0) == []


def test_linear_law_endpoints_and_midpoint():
    m = mixer()
    add(m, "left", pan=-1.0)
    add(m, "right", pan=1.0)
    add(m, "mid", pan=0.5)

    assert m.meter("left")["left"] == pytest.approx(1.0)
    assert m.meter("left")["right"] == pytest.approx(0.0)
    assert m.meter("right")["left"] == pytest.approx(0.0)
    assert m.meter("right")["right"] == pytest.approx(1.0)
    assert m.meter("mid")["left"] == pytest.approx(0.25)
    assert m.meter("mid")["right"] == pytest.approx(0.75)


def test_equal_power_law_keeps_power_constant_at_offset():
    m = mixer()
    add(m, "hard", pan=-1.0)
    add(m, "off", pan=0.5)
    assert m.set_pan_law("equal_power") == "equal_power"

    hard = m.meter("hard")
    assert hard["left"] == pytest.approx(1.0, abs=1e-9)
    assert hard["right"] == pytest.approx(0.0, abs=1e-9)

    off = m.meter("off")
    theta = math.pi * 3.0 / 8.0
    assert off["left"] == pytest.approx(math.cos(theta), abs=1e-6)
    assert off["right"] == pytest.approx(math.sin(theta), abs=1e-6)
    assert off["left"] ** 2 + off["right"] ** 2 == pytest.approx(1.0, abs=1e-6)


def test_minus_six_db_law_scales_equal_power_down():
    m = mixer()
    add(m, "edge", pan=1.0)
    add(m, "off", pan=0.5)
    m.set_pan_law("minus_6db")

    edge = m.meter("edge")
    assert edge["left"] == pytest.approx(0.0, abs=1e-9)
    assert edge["right"] == pytest.approx(1.0 / math.sqrt(2.0), abs=1e-6)

    off = m.meter("off")
    theta = math.pi * 3.0 / 8.0
    assert off["left"] == pytest.approx(math.cos(theta) / math.sqrt(2.0), abs=1e-6)
    assert off["right"] == pytest.approx(math.sin(theta) / math.sqrt(2.0), abs=1e-6)


def test_pan_ramp_moves_one_step_per_sample():
    m = mixer()
    add(m, "s1", pan=0.0)
    assert m.set_pan("s1", 1.0, ramp_samples=4) == 4
    assert m.ramp_remaining("s1") == 4

    frames = m.render(4)
    assert [round(row[0], 6) for row in frames] == [0.375, 0.25, 0.125, 0.0]
    assert [round(row[1], 6) for row in frames] == [0.625, 0.75, 0.875, 1.0]
    assert m.ramp_remaining("s1") == 0
    assert m.meter("s1")["pan"] == pytest.approx(1.0)
    assert m.stats()["ramps_started"] == 1


def test_gain_ramp_reaches_target_and_clears_counter():
    m = mixer()
    add(m, "s1", pan=0.0, gain=1.0)
    assert m.set_gain("s1", 0.0, ramp_samples=2) == 2

    frames = m.render(2)
    assert frames[0][0] == pytest.approx(0.25)
    assert frames[0][1] == pytest.approx(0.25)
    assert frames[1][0] == pytest.approx(0.0)
    assert frames[1][1] == pytest.approx(0.0)
    assert m.ramp_remaining("s1") == 0
    assert m.meter("s1")["gain"] == pytest.approx(0.0)


def test_render_sums_every_source_per_sample():
    m = mixer()
    add(m, "a", pan=-1.0)
    add(m, "b", pan=1.0)

    frames = m.render(3)
    assert len(frames) == 3
    assert frames[2][0] == pytest.approx(1.0)
    assert frames[2][1] == pytest.approx(1.0)
    assert m.stats()["rendered_frames"] == 3


def test_law_switch_applies_to_next_sample():
    m = mixer()
    add(m, "s1", pan=0.5)
    assert m.meter("s1")["left"] == pytest.approx(0.25)

    m.set_pan_law("equal_power", fade_samples=0)
    theta = math.pi * 3.0 / 8.0
    assert m.meter("s1")["left"] == pytest.approx(math.cos(theta), abs=1e-6)
    assert m.stats()["law_switches"] == 1
    assert m.render(1)[0][0] == pytest.approx(math.cos(theta), abs=1e-6)


def test_law_switch_with_fade_crossfades_over_samples():
    m = mixer()
    add(m, "s1", pan=0.5)
    m.set_pan_law("equal_power")
    m.set_pan_law("linear", fade_samples=4)

    frames = m.render(4)
    assert frames[0][0] == pytest.approx(0.349513, abs=1e-5)
    assert frames[1][0] == pytest.approx(0.316342, abs=1e-5)
    assert frames[2][0] == pytest.approx(0.283171, abs=1e-5)
    assert frames[3][0] == pytest.approx(0.25, abs=1e-6)
    assert frames[3][1] == pytest.approx(0.75, abs=1e-6)


def test_meter_reports_coefficients_gain_and_ramp():
    m = mixer()
    add(m, "s1", pan=0.5, gain=0.5)

    info = m.meter("s1")
    assert set(info) == {"source_id", "pan", "gain", "left", "right",
                         "ramp_remaining", "pan_law"}
    assert info["left"] == pytest.approx(0.125)
    assert info["right"] == pytest.approx(0.375)
    assert info["ramp_remaining"] == 0
    assert info["pan_law"] == "linear"


def test_remove_source_drops_it_from_listing():
    m = mixer()
    add(m, "s1")
    add(m, "s2")
    m.set_pan("s1", 1.0, ramp_samples=2)
    m.render(2)

    assert m.remove_source("s1", immediate=True) == 0
    assert m.sources() == ["s2"]
    assert m.stats()["removed"] == 1


def test_snapshot_is_detached_and_restore_roundtrips():
    m = mixer()
    add(m, "s1", pan=0.5, gain=0.75)
    m.set_pan_law("equal_power")

    state = m.snapshot()
    pristine = copy.deepcopy(state)
    m.set_pan("s1", -1.0)
    m.set_pan_law("linear")
    assert m.meter("s1")["pan"] == pytest.approx(-1.0)
    assert m.meter("s1")["pan_law"] == "linear"

    clone = mixer()
    clone.restore(pristine)
    assert clone.sources() == ["s1"]
    assert clone.meter("s1")["pan_law"] == "equal_power"
    assert clone.meter("s1")["pan"] == pytest.approx(0.5)
    assert clone.meter("s1")["gain"] == pytest.approx(0.75)

    for bad in (None, {}, [], "state"):
        with pytest.raises(plm.PanLawError) as exc:
            clone.restore(bad)
        assert exc.value.code == "PAN_SNAPSHOT_INVALID"
    assert clone.meter("s1")["pan"] == pytest.approx(0.5)


def test_concurrent_adds_keep_every_source():
    m = mixer(max_sources=8)
    ids = [f"s{index:02d}" for index in range(8)]
    errors = []
    barrier = threading.Barrier(len(ids))

    def worker(source_id):
        try:
            barrier.wait(timeout=5)
            m.add_source(source_id, pan=0.0)
        except Exception as exc:  # pragma: no cover - surfaced through errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(source_id,)) for source_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert m.sources() == ids
    assert m.stats()["sources"] == 8
