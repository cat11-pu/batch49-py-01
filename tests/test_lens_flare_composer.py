import threading

import pytest

import lens_flare_composer as lf


def build(**kwargs):
    composer = lf.LensFlareComposer(**kwargs)
    composer.set_source(0.5, -0.25, 1.0)
    return composer


def test_budget_reports_limits_before_any_frame():
    composer = lf.LensFlareComposer()
    assert composer.size() == (1920, 1080)
    assert composer.budget() == {"frames": 0, "elements": 0, "samples": 16,
                                 "flares_last_frame": 0}


def test_layout_is_mirrored_chain_through_center():
    composer = build()
    composer.add_element("a", 0.0, 1.0, 0.05)
    composer.add_element("b", 1.0, 0.8, 0.05)
    composer.add_element("c", 2.0, 0.6, 0.05)
    assert composer.layout_elements() == [("a", 0.5, -0.25),
                                          ("b", 0.0, 0.0),
                                          ("c", -0.5, 0.25)]


def test_add_element_rejects_bad_definition():
    composer = build()
    composer.add_element("a", 0.5, 1.0, 0.05)
    bad_calls = [("a", 1.0, 1.0, 0.05), ("x", 2.5, 1.0, 0.05),
                 ("y", 1.0, 0.0, 0.05), ("z", 1.0, 1.0, -0.1)]
    for args in bad_calls:
        with pytest.raises(lf.LensFlareError) as exc:
            composer.add_element(*args)
        assert exc.value.code == "FLARE_INVALID_ELEMENT"
    assert [row[0] for row in composer.layout_elements()] == ["a"]


def test_element_count_cap_keeps_existing_elements():
    composer = build(max_elements=2)
    composer.add_element("a", 0.0, 1.0, 0.05)
    composer.add_element("b", 1.0, 1.0, 0.05)
    with pytest.raises(lf.LensFlareError) as exc:
        composer.add_element("c", 1.5, 1.0, 0.05)
    assert exc.value.code == "FLARE_ELEMENT_LIMIT"
    assert [row[0] for row in composer.layout_elements()] == ["a", "b"]


def test_resize_rejects_non_positive_dimensions():
    composer = build()
    for width, height in [(0, 1080), (1920, -1)]:
        with pytest.raises(lf.LensFlareError) as exc:
            composer.resize(width, height)
        assert exc.value.code == "FLARE_INVALID_SIZE"
    assert composer.size() == (1920, 1080)


def test_pixel_offset_uses_current_size():
    composer = build()
    composer.add_element("a", 1.0, 1.0, 0.05)
    composer.add_element("b", 0.0, 1.0, 0.05)
    assert composer.pixel_offset("a") == (0.0, 0.0)
    assert composer.pixel_offset("b") == (480.0, -135.0)


def test_occlusion_without_occluders_is_full():
    composer = build()
    composer.add_element("a", 0.0, 0.9, 0.1)
    assert composer.occlusion_sample("a") == 1.0
    assert composer.intensity("a") == pytest.approx(0.9)


def test_full_occluder_blacks_out_element():
    composer = build()
    composer.add_element("a", 1.0, 0.9, 0.1)
    composer.add_occluder(-0.2, -0.2, 0.2, 0.2)
    assert composer.occlusion_sample("a") == 0.0
    assert composer.intensity("a") == 0.0
    assert composer.compose_frame() == []


def test_clip_returns_full_box_when_inside():
    composer = build()
    composer.add_element("a", 1.0, 1.0, 0.1)
    assert composer.clip_to_screen("a") == (-0.1, -0.1, 0.1, 0.1)


def test_clip_returns_none_when_fully_outside():
    composer = lf.LensFlareComposer()
    composer.set_source(1.5, 0.4, 1.0)
    composer.add_element("a", 0.0, 1.0, 0.1)
    assert composer.clip_to_screen("a") is None


def test_compose_frame_caps_and_orders_by_intensity():
    composer = build(max_flares_per_frame=2)
    composer.add_element("dim", 0.4, 0.3, 0.05)
    composer.add_element("bright", 1.0, 0.9, 0.05)
    composer.add_element("mid", 1.6, 0.6, 0.05)
    frame = composer.compose_frame()
    assert [entry["id"] for entry in frame] == ["bright", "mid"]
    assert [round(entry["intensity"], 6) for entry in frame] == [0.9, 0.6]
    assert composer.budget()["flares_last_frame"] == 2
    assert composer.budget()["frames"] == 1


def test_compose_uses_clipped_box_for_partial_elements():
    composer = lf.LensFlareComposer()
    composer.set_source(0.95, 0.0, 1.0)
    composer.add_element("edge", 0.0, 1.0, 0.1)
    frame = composer.compose_frame()
    assert [entry["id"] for entry in frame] == ["edge"]
    assert frame[0]["box"] == (0.85, -0.1, 1.0, 0.1)


def test_snapshot_restore_roundtrip():
    composer = build(max_flares_per_frame=4)
    composer.add_element("a", 0.5, 0.7, 0.1)
    composer.add_occluder(0.0, 1.2, 0.2, 1.4)
    composer.compose_frame()
    state = composer.snapshot()
    clone = lf.LensFlareComposer()
    clone.restore(state)
    assert clone.size() == composer.size()
    assert clone.layout_elements() == composer.layout_elements()
    assert clone.intensity("a") == pytest.approx(composer.intensity("a"))
    assert clone.budget() == composer.budget()


def test_restore_rejects_malformed_snapshot():
    composer = build()
    composer.add_element("a", 1.0, 1.0, 0.1)
    good = composer.snapshot()
    bad_states = [None, {}, dict(good, version=2),
                  {key: value for key, value in good.items() if key != "counters"},
                  dict(good, size=[1920]),
                  dict(good, elements="nope")]
    for bad in bad_states:
        with pytest.raises(lf.LensFlareError) as exc:
            composer.restore(bad)
        assert exc.value.code == "FLARE_SNAPSHOT_INVALID"
    assert [row[0] for row in composer.layout_elements()] == ["a"]


def test_concurrent_source_updates_keep_layout_coherent():
    composer = lf.LensFlareComposer()
    composer.add_element("a", 0.5, 1.0, 0.05)
    composer.add_element("b", 1.0, 1.0, 0.05)
    errors = []

    def writer(base):
        try:
            for step in range(120):
                value = base + step * 0.001
                composer.set_source(value, value, 1.0)
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    def reader():
        try:
            for _ in range(200):
                for _name, nx, ny in composer.layout_elements():
                    assert abs(nx - ny) < 1e-9
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(repr(exc))

    threads = [threading.Thread(target=writer, args=(0.1,)),
               threading.Thread(target=writer, args=(0.2,)),
               threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
